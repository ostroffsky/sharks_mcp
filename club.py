"""Client for the HC Sharks Kids student portal."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx


ORIGIN = "https://hcsharkskids.tvoyklass.com"
MOSCOW = ZoneInfo("Europe/Moscow")


class ClubError(Exception):
    """Safe to show to the caller."""


def today():
    return datetime.now(MOSCOW).date()


class Club:
    def __init__(self, http):
        self.http = http
        self.account = {}

    def request(self, method, path, *, params=None, payload=None, mutation=False, expected=dict):
        try:
            response = self.http.request(method, path, params=params, json=payload)
        except httpx.RequestError:
            if mutation:
                raise ClubError("Связь прервалась. Проверьте свои записи перед повтором.") from None
            raise ClubError("Не удалось связаться с личным кабинетом.") from None
        if mutation and response.status_code >= 500:
            raise ClubError("Кабинет вернул ошибку. Проверьте свои записи перед повтором.")
        if response.status_code in (401, 403):
            raise ClubError("Кабинет отклонил доступ. Проверьте вход и права ученика.")
        if not response.is_success:
            raise ClubError(f"Кабинет отклонил запрос (HTTP {response.status_code}).")
        if mutation:
            return
        try:
            data = response.json()
        except ValueError:
            raise ClubError("Кабинет вернул неожиданный формат ответа.") from None
        if not isinstance(data, expected):
            raise ClubError("Неизвестный формат ответа кабинета.")
        return data

    def login(self, email, password):
        data = self.request("POST", "auth/login", payload={"login": email, "password": password})
        token = data.get("csrfToken")
        if not isinstance(token, str) or not token:
            raise ClubError("Кабинет не подтвердил вход.")
        self.http.headers["x-csrf-token"] = token
        self.account = self.request("GET", "baseInfo")
        if not self.account.get("account") or not isinstance(self.account.get("users"), list):
            raise ClubError("Кабинет не подтвердил доступ к профилю.")

    def student(self, student_id):
        users = self.account["users"]
        if student_id is None and len(users) == 1:
            return users[0]
        for user in users:
            if user["id"] == student_id:
                return user
        choices = ", ".join(f"{u['id']} ({u.get('name', '')})" for u in users)
        raise ClubError(f"Укажите student_id. Ученики в этом аккаунте: {choices or 'нет'}.")

    def lookup(self, collection, item_id):
        return next((row for row in self.account.get(collection, []) if row["id"] == item_id), {})

    @staticmethod
    def own_record(lesson, student_id):
        # scopeUserId makes the portal return records for the selected student.
        return next((r for r in lesson.get("records", []) if r.get("userId", student_id) == student_id), None)

    def lesson_summary(self, lesson, student_id):
        group = self.lookup("classes", lesson["classId"])
        branch = self.lookup("filials", lesson.get("filialId") or group.get("filialId"))
        record = self.own_record(lesson, student_id)
        return {
            "lesson_id": lesson["id"], "date": lesson["date"], "begin": lesson["beginTime"],
            "end": lesson.get("endTime"), "timezone": branch.get("timezone"),
            "class_id": lesson["classId"], "group": group.get("name"),
            "course": self.lookup("courses", group.get("courseId")).get("name"),
            "location": branch.get("name"), "room": self.lookup("rooms", lesson.get("roomId")).get("name"),
            "free_places": lesson.get("freeRecords"), "booked": record is not None,
        }

    def lessons(self, start_date, end_date, student_id, mine):
        user = self.student(student_id)
        start = start_date or today()
        end = end_date or start + timedelta(days=6)
        if end < start:
            raise ClubError("Конец периода должен быть не раньше начала.")
        params = {"scopeUserId": user["id"], "date[]": [start.isoformat(), end.isoformat()],
                  "includeRecords": True, "countFreeRecords": True, "sort": "date", "limit": 100}
        if mine:
            params["userId"] = user["id"]
        lessons = {}
        offset = 0
        while True:
            page = self.request("GET", "lessons", params={**params, "offset": offset})
            rows = page["lessons"]
            previous = len(lessons)
            for row in rows:
                lessons[row["id"]] = self.lesson_summary(row, user["id"])
            offset += len(rows)
            total = page["stats"]["totalItems"]
            if offset >= total:
                break
            if len(lessons) == previous:
                raise ClubError("Кабинет не вернул следующую страницу расписания.")
        items = sorted(lessons.values(), key=lambda item: (item["date"], item["begin"], item["lesson_id"]))
        return {"student_id": user["id"], "from": start.isoformat(), "to": end.isoformat(), "lessons": items}

    def subscriptions(self, student_id, include_inactive):
        user = self.student(student_id)
        data = self.request("GET", "subscriptions", params={
            "includeFamilySubs": True, "userId": user["id"], "date[]": ["2016-01-01", today().isoformat()]})
        items = []
        for sub in data["subscriptions"]:
            active = sub["statusId"] == 2
            if active or include_inactive:
                items.append({"id": sub["id"], "name": (sub.get("Subscription") or {}).get("name"),
                              "active": active, "status_id": sub["statusId"],
                              "begin_date": sub.get("beginDate"), "end_date": sub.get("endDate")})
        return {"student_id": user["id"], "subscriptions": items}

    def cancellation_target(self, lesson_id, student_id):
        lesson = self.request("GET", f"lessons/{lesson_id}",
                              params={"scopeUserId": student_id, "includeRecords": True})
        records = lesson.get("records")
        if lesson.get("id") != lesson_id or not isinstance(records, list):
            raise ClubError("Кабинет не подтвердил занятие или список записей.")
        target = None
        for record in records:
            if (not isinstance(record, dict) or type(record.get("userId")) is not int
                    or record.get("lessonId") != lesson_id):
                raise ClubError("Кабинет не подтвердил принадлежность записи.")
            if record["userId"] == student_id:
                record_id = record.get("id")
                if target is not None or type(record_id) is not int or record_id <= 0:
                    raise ClubError("Не удалось однозначно определить запись ученика.")
                target = record_id
        return lesson, target

    def cancel(self, lesson_id, student_id):
        student_id = self.student(student_id)["id"]
        result = {"student_id": student_id, "lesson_id": lesson_id}
        lesson, record_id = self.cancellation_target(lesson_id, student_id)
        if record_id is None:
            return {**result, "status": "not_booked"}

        settings = self.request("GET", "lkSettings")
        mode = settings.get("cancelRecords")
        group = self.lookup("classes", lesson["classId"])
        if (not group or lesson.get("status") != 0 or not mode or mode == "no"
                or (mode != "yes" and not group.get("allowCancelRecords"))):
            raise ClubError("Кабинет не разрешает отмену записи на это занятие.")
        branch = self.lookup("filials", group.get("filialId"))
        try:
            zone = ZoneInfo(branch["timezone"])
            start = datetime.fromisoformat(f"{lesson['date']}T{lesson['beginTime']}").replace(tzinfo=zone)
            minutes = int(settings["cancelRecordsTimeout"])
            if minutes < 0:
                raise ValueError
            cutoff = start - timedelta(minutes=minutes)
        except (KeyError, ValueError, TypeError, OverflowError):
            raise ClubError("Не удалось определить срок отмены по настройкам клуба.") from None
        if datetime.now(zone) > cutoff:
            raise ClubError(f"Срок отмены истёк {cutoff:%d.%m.%Y в %H:%M} ({zone.key}).")

        self.request("DELETE", f"lessonRecords/{record_id}", mutation=True)
        try:
            _, remaining = self.cancellation_target(lesson_id, student_id)
            if remaining is None:
                return {**result, "status": "cancelled"}
        except ClubError:
            pass
        return {**result, "status": "submitted_unconfirmed",
                "message": "Кабинет принял запрос. Проверьте свои записи перед повтором."}

    def book(self, lesson_id, student_id, allow_time_overlap):
        user = self.student(student_id)
        detail_path = f"lessons/{lesson_id}"
        params = {"scopeUserId": user["id"], "includeRecords": True,
                  "countFreeRecords": True, "includeFreezes": True}
        lesson = self.request("GET", detail_path, params=params)
        if lesson.get("id") != lesson_id or not isinstance(lesson.get("records"), list):
            raise ClubError("Кабинет не подтвердил занятие или список записей.")
        result = {"student_id": user["id"], "lesson_id": lesson_id}
        if self.own_record(lesson, user["id"]) is not None:
            return {**result, "status": "already_booked"}

        settings = self.request("GET", "lkSettings")
        group = self.lookup("classes", lesson["classId"])
        branch = self.lookup("filials", group.get("filialId"))
        joined = any(j["classId"] == lesson["classId"] for j in user["joins"])
        if (not settings.get("allowRecords") or not group.get("showSelectLessons")
                or group.get("courseId") == 0 or lesson["status"] != 0
                or lesson.get("freeRecords") == 0 or user["id"] in (lesson.get("frozenUsers") or [])
                or (not joined and (settings.get("viewClasses") != "all"
                                    or group.get("status") != "opened" or group.get("freeRecords") == 0))
                or (settings.get("viewFilials") == "own" and branch.get("id") not in user["filials"])):
            raise ClubError("Запись на это групповое занятие недоступна ученику.")
        zone = ZoneInfo(branch["timezone"])
        start = datetime.fromisoformat(f"{lesson['date']}T{lesson['beginTime']}").replace(tzinfo=zone)
        now = datetime.now(zone)
        cutoff = start - timedelta(minutes=int(settings.get("allowRecordsTimeout") or 0))
        last_day = now.date() + timedelta(days=int(settings["daysForRecord"]))
        if cutoff < now or cutoff.date() > last_day:
            raise ClubError("Сейчас запись на это занятие закрыта.")
        if not allow_time_overlap:
            overlaps = self.request("POST", "lessonRecords/intersection", expected=list,
                                    payload={"userId": user["id"], "lessonIds": [lesson_id]})
            if overlaps:
                raise ClubError("Есть пересечение с другим занятием. Для записи с пересечением укажите allow_time_overlap=true.")

        if joined:
            self.request("POST", "lessonRecords", mutation=True,
                         payload={"userId": user["id"], "lessonId": lesson_id})
        else:
            self.request("POST", "joins", mutation=True,
                         payload={"userId": user["id"], "classId": group["id"], "lessonIds": [lesson_id]})
        try:
            updated = self.request("GET", detail_path, params=params)
            if self.own_record(updated, user["id"]) is not None:
                return {**result, "status": "booked"}
        except ClubError:
            pass
        return {**result, "status": "submitted_unconfirmed",
                "message": "Кабинет принял запрос. Проверьте свои записи перед повтором."}


@contextmanager
def session(email, password):
    with httpx.Client(base_url=ORIGIN + "/api/v1/user/", timeout=30, follow_redirects=False,
                      headers={"Accept": "application/json", "Origin": ORIGIN, "Referer": ORIGIN + "/"}) as http:
        client = Club(http)
        client.login(email, password)
        yield client
