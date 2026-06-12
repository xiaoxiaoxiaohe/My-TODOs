from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests


IMPORT_RANGE_DAYS = 30
FEISHU_PREFIX = "[飞书]"


class FeishuImportError(Exception):
    """Raised for Feishu import related errors."""


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    bitable_app_token: str = ""
    bitable_table_id: str = ""
    bitable_view_id: str = ""
    field_title: str = ""
    field_date: str = ""
    field_daily_report: str = ""
    bitable_enabled: bool = True
    report_api_enabled: bool = True
    timeout_seconds: int = 10


class FeishuAuthClient:
    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

    def __init__(self, app_id: str, app_secret: str, timeout_seconds: int = 10):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.timeout_seconds = timeout_seconds
        self._token: Optional[str] = None
        self._expire_at_ts: float = 0.0

    def get_tenant_access_token(self, force_refresh: bool = False) -> str:
        now_ts = datetime.now(timezone.utc).timestamp()
        if (not force_refresh) and self._token and now_ts < (self._expire_at_ts - 60):
            return self._token

        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                payload = {
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                }
                response = self._request_json("POST", self.TOKEN_URL, json_data=payload)
                token = (response.get("tenant_access_token") or "").strip()
                if token == "":
                    raise FeishuImportError("鉴权失败：未返回 tenant_access_token")
                expire_sec = int(response.get("expire", 7200))
                self._token = token
                self._expire_at_ts = now_ts + max(300, expire_sec)
                return token
            except FeishuImportError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise FeishuImportError("鉴权失败")

    def request_with_token(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        token = self.get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        return self._request_json(method, url, headers=headers, params=params, json_data=json_data)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except requests.Timeout as exc:
            raise FeishuImportError("网络超时") from exc
        except requests.RequestException as exc:
            raise FeishuImportError(f"网络错误: {exc}") from exc

        try:
            payload = resp.json()
        except ValueError as exc:
            raise FeishuImportError("接口返回非JSON数据") from exc

        code = payload.get("code", 0)
        if code != 0:
            msg = (
                payload.get("msg")
                or payload.get("message")
                or payload.get("error")
                or payload.get("error_msg")
                or f"接口返回错误 code={code}"
            )
            raise FeishuImportError(str(msg))

        return payload.get("data", {})


class FeishuBitableImporter:
    def __init__(self, auth_client: FeishuAuthClient, config: FeishuConfig):
        self.auth = auth_client
        self.config = config

    def ping(self) -> None:
        url = self._records_url()
        params: Dict[str, Any] = {"page_size": 1}
        if self.config.bitable_view_id.strip() != "":
            params["view_id"] = self.config.bitable_view_id.strip()
        self.auth.request_with_token("GET", url, params=params)

    def fetch_tasks(self) -> List[Dict[str, str]]:
        tasks: List[Dict[str, str]] = []
        url = self._records_url()
        page_token = ""
        page_count = 0

        while True:
            params: Dict[str, Any] = {"page_size": 200}
            if self.config.bitable_view_id.strip() != "":
                params["view_id"] = self.config.bitable_view_id.strip()
            if page_token:
                params["page_token"] = page_token

            data = self.auth.request_with_token("GET", url, params=params)
            items = data.get("items") or []

            for item in items:
                fields = item.get("fields") or {}
                if self.config.field_title not in fields:
                    raise FeishuImportError("表格字段不存在：标题字段")
                if self.config.field_date not in fields:
                    raise FeishuImportError("表格字段不存在：日期字段")

                title = _to_text(fields.get(self.config.field_title)).strip()
                date_str = _normalize_to_yyyy_mm_dd(fields.get(self.config.field_date))
                description = ""
                if self.config.field_daily_report.strip() != "":
                    if self.config.field_daily_report not in fields:
                        raise FeishuImportError("表格字段不存在：日汇报字段")
                    description = _to_text(fields.get(self.config.field_daily_report)).strip()

                tasks.append(
                    {
                        "source": "bitable",
                        "title": title,
                        "date": date_str,
                        "description": description,
                    }
                )

            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "")
            page_count += 1
            if (not has_more) or page_token == "" or page_count >= 50:
                break

        return tasks

    def _records_url(self) -> str:
        return (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/"
            f"{self.config.bitable_app_token}/tables/{self.config.bitable_table_id}/records"
        )


class FeishuReportImporter:
    REPORT_ENDPOINTS = [
        "https://open.feishu.cn/open-apis/report/v1/reports",
        "https://open.feishu.cn/open-apis/report/v1/list",
        "https://open.feishu.cn/open-apis/report/v1/report/list",
    ]

    TITLE_KEYS = ["title", "report_title", "name", "subject"]
    DESCRIPTION_KEYS = ["summary", "content", "body", "text", "report_content"]
    DATE_KEYS = [
        "report_date",
        "submit_time",
        "submit_at",
        "created_at",
        "create_time",
        "updated_at",
        "update_time",
        "date",
    ]

    def __init__(self, auth_client: FeishuAuthClient, config: FeishuConfig):
        self.auth = auth_client
        self.config = config

    def ping(self, start_date: date, end_date: date) -> None:
        self._fetch_with_auto_endpoint(start_date=start_date, end_date=end_date, page_size=1)

    def fetch_tasks(self, start_date: date, end_date: date) -> List[Dict[str, str]]:
        return self._fetch_with_auto_endpoint(start_date=start_date, end_date=end_date, page_size=50)

    def _fetch_with_auto_endpoint(self, start_date: date, end_date: date, page_size: int) -> List[Dict[str, str]]:
        errors: List[str] = []
        for endpoint in self.REPORT_ENDPOINTS:
            try:
                return self._fetch_from_endpoint(endpoint, start_date=start_date, end_date=end_date, page_size=page_size)
            except FeishuImportError as exc:
                errors.append(str(exc))

        if errors:
            raise FeishuImportError(f"汇报接口不可用/未授权: {errors[-1]}")
        raise FeishuImportError("汇报接口不可用/未授权")

    def _fetch_from_endpoint(self, endpoint: str, start_date: date, end_date: date, page_size: int) -> List[Dict[str, str]]:
        tasks: List[Dict[str, str]] = []
        page_token = ""
        page_count = 0
        start_ts = int(datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc).timestamp())

        while True:
            params: Dict[str, Any] = {
                "page_size": page_size,
                "start_time": start_ts,
                "end_time": end_ts,
            }
            if page_token:
                params["page_token"] = page_token

            data = self.auth.request_with_token("GET", endpoint, params=params)
            items = _extract_report_items(data)

            for item in items:
                title = _pick_by_keys(item, self.TITLE_KEYS).strip()
                date_str = ""
                for k in self.DATE_KEYS:
                    date_str = _normalize_to_yyyy_mm_dd(item.get(k))
                    if date_str:
                        break
                description = _pick_by_keys(item, self.DESCRIPTION_KEYS).strip()
                tasks.append(
                    {
                        "source": "report",
                        "title": title,
                        "date": date_str,
                        "description": description,
                    }
                )

            has_more = _extract_has_more(data)
            page_token = _extract_page_token(data)
            page_count += 1
            if (not has_more) or page_token == "" or page_count >= 20:
                break

        return tasks


class FeishuImportService:
    def __init__(self, config: FeishuConfig):
        self.config = config
        self.auth = FeishuAuthClient(
            app_id=config.app_id,
            app_secret=config.app_secret,
            timeout_seconds=config.timeout_seconds,
        )
        self.bitable = FeishuBitableImporter(self.auth, config)
        self.report = FeishuReportImporter(self.auth, config)

    def validate_required_config(self) -> None:
        app_id = self.config.app_id.strip()
        app_secret = self.config.app_secret.strip()
        if app_id == "" or app_secret == "":
            raise FeishuImportError("鉴权失败：请先在设置里填写 App ID 和 App Secret")

        if not self.config.bitable_enabled and not self.config.report_api_enabled:
            raise FeishuImportError("请至少启用一个飞书导入来源")

        if self.config.bitable_enabled:
            missing = []
            if self.config.bitable_app_token.strip() == "":
                missing.append("FEISHU_BITABLE_APP_TOKEN")
            if self.config.bitable_table_id.strip() == "":
                missing.append("FEISHU_BITABLE_TABLE_ID")
            if self.config.field_title.strip() == "":
                missing.append("FEISHU_FIELD_TITLE")
            if self.config.field_date.strip() == "":
                missing.append("FEISHU_FIELD_DATE")
            if self.config.field_daily_report.strip() == "":
                missing.append("FEISHU_FIELD_DAILY_REPORT")
            if missing:
                raise FeishuImportError("配置缺失: " + ", ".join(missing))

    def test_connection(self, start_date: date, end_date: date) -> Dict[str, Any]:
        self.validate_required_config()
        result = {
            "ok": True,
            "details": {
                "auth": "ok",
                "bitable": "disabled",
                "report": "disabled",
            },
            "errors": {},
        }

        self.auth.get_tenant_access_token(force_refresh=True)

        if self.config.bitable_enabled:
            try:
                self.bitable.ping()
                result["details"]["bitable"] = "ok"
            except FeishuImportError as exc:
                result["details"]["bitable"] = "error"
                result["errors"]["bitable"] = str(exc)

        if self.config.report_api_enabled:
            try:
                self.report.ping(start_date=start_date, end_date=end_date)
                result["details"]["report"] = "ok"
            except FeishuImportError as exc:
                result["details"]["report"] = "error"
                result["errors"]["report"] = str(exc)

        if result["errors"]:
            result["ok"] = False
        return result

    def import_tasks(
        self,
        *,
        existing_keys: Set[str],
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        self.validate_required_config()

        output_tasks: List[Dict[str, str]] = []
        seen_keys = set(existing_keys)

        result: Dict[str, Any] = {
            "tasks": output_tasks,
            "added": 0,
            "skipped_duplicate": 0,
            "skipped_history": 0,
            "skipped_out_of_range": 0,
            "skipped_invalid": 0,
            "failed_sources": 0,
            "source_errors": {},
        }

        all_raw_tasks: List[Dict[str, str]] = []

        if self.config.bitable_enabled:
            try:
                all_raw_tasks.extend(self.bitable.fetch_tasks())
            except FeishuImportError as exc:
                result["failed_sources"] += 1
                result["source_errors"]["bitable"] = str(exc)

        if self.config.report_api_enabled:
            try:
                all_raw_tasks.extend(self.report.fetch_tasks(start_date=start_date, end_date=end_date))
            except FeishuImportError as exc:
                result["failed_sources"] += 1
                result["source_errors"]["report"] = str(exc)

        for raw_task in all_raw_tasks:
            title = (raw_task.get("title") or "").strip()
            task_date = _normalize_to_yyyy_mm_dd(raw_task.get("date"))
            description = (raw_task.get("description") or "").strip()

            if title == "" or task_date == "":
                result["skipped_invalid"] += 1
                continue

            parsed_date = _parse_yyyy_mm_dd(task_date)
            if parsed_date is None:
                result["skipped_invalid"] += 1
                continue
            if parsed_date < start_date:
                result["skipped_history"] += 1
                continue
            if parsed_date > end_date:
                result["skipped_out_of_range"] += 1
                continue

            prefixed_title = f"{FEISHU_PREFIX} {title}".strip()
            dedupe_key = f"{task_date}|{prefixed_title.strip()}"
            if dedupe_key in seen_keys:
                result["skipped_duplicate"] += 1
                continue

            seen_keys.add(dedupe_key)
            output_tasks.append(
                {
                    "source": raw_task.get("source", "unknown"),
                    "date": task_date,
                    "title": prefixed_title,
                    "description": description,
                }
            )

        result["added"] = len(output_tasks)
        return result


def _parse_yyyy_mm_dd(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_to_yyyy_mm_dd(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, datetime):
        return value.date().strftime("%Y-%m-%d")

    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000.0
        try:
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
            return dt.date().strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""

    if isinstance(value, list):
        for item in value:
            normalized = _normalize_to_yyyy_mm_dd(item)
            if normalized:
                return normalized
        return ""

    if isinstance(value, dict):
        for key in ("date", "start_date", "start", "text", "value", "timestamp", "time"):
            if key in value:
                normalized = _normalize_to_yyyy_mm_dd(value.get(key))
                if normalized:
                    return normalized
        return ""

    text = str(value).strip()
    if text == "":
        return ""

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        maybe_date = text[:10]
        if _parse_yyyy_mm_dd(maybe_date) is not None:
            return maybe_date

    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    if text.isdigit():
        return _normalize_to_yyyy_mm_dd(int(text))

    return ""


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_to_text(v).strip() for v in value]
        return " ".join([p for p in parts if p])
    if isinstance(value, dict):
        for key in ("text", "name", "title", "value", "content", "url"):
            if key in value:
                return _to_text(value.get(key))
        return str(value)
    return str(value)


def _pick_by_keys(data: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        if key in data:
            text = _to_text(data.get(key)).strip()
            if text:
                return text
    return ""


def _extract_report_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "report_list", "reports", "list"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    page_data = data.get("page")
    if isinstance(page_data, dict):
        items = page_data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _extract_has_more(data: Dict[str, Any]) -> bool:
    if "has_more" in data:
        return bool(data.get("has_more"))
    page = data.get("page")
    if isinstance(page, dict):
        return bool(page.get("has_more"))
    return False


def _extract_page_token(data: Dict[str, Any]) -> str:
    for key in ("page_token", "next_page_token", "cursor"):
        token = data.get(key)
        if token:
            return str(token)
    page = data.get("page")
    if isinstance(page, dict):
        for key in ("page_token", "next_page_token", "cursor"):
            token = page.get(key)
            if token:
                return str(token)
    return ""
