import json
import unittest
from datetime import timedelta

import httpx

from club import Club, ClubError, ORIGIN, today


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.user = {"id": 7, "name": "Ученик", "joins": [{"classId": 4}], "filials": [1]}
        self.group = {"id": 4, "courseId": 8, "filialId": 1, "name": "Группа", "status": "opened",
                      "showSelectLessons": True}
        self.lesson = {"id": 9, "classId": 4, "date": (today() + timedelta(days=1)).isoformat(),
                       "beginTime": "12:00", "endTime": "13:00", "status": 0, "records": [], "freeRecords": 1}
        self.settings = {"allowRecords": True, "daysForRecord": "30", "allowRecordsTimeout": 30,
                         "viewClasses": "all", "viewFilials": "own",
                         "cancelRecords": "yes", "cancelRecordsTimeout": 240}
        self.users = [self.user]
        self.writes = []
        self.overlap = []
        self.lose_response = False
        self.delay_delete = False
        self.http = httpx.Client(base_url=ORIGIN + "/api/v1/user/", transport=httpx.MockTransport(self.reply))
        self.addCleanup(self.http.close)
        self.client = Club(self.http)
        self.client.login("sample@example.invalid", "fake")

    def reply(self, req):
        path = req.url.path.removeprefix("/api/v1/user/")
        if path == "auth/login":
            self.assertEqual(json.loads(req.content), {"login": "sample@example.invalid", "password": "fake"})
            return httpx.Response(200, headers={"Set-Cookie": "sid=example; Secure; Path=/"}, json={"csrfToken": "sample-token"})
        self.assertEqual(req.headers["x-csrf-token"], "sample-token")
        self.assertEqual(req.headers["Cookie"], "sid=example")
        if path == "baseInfo":
            data = {"account": {"id": 1}, "users": self.users, "classes": [self.group],
                    "courses": [{"id": 8, "name": "Хоккей"}], "filials": [{"id": 1, "timezone": "Europe/Moscow"}]}
        elif path == "lessons":
            self.assertEqual(req.url.params.get_list("date[]"), [today().isoformat(), (today()+timedelta(days=6)).isoformat()])
            self.assertEqual(req.url.params["scopeUserId"], "7")
            if req.url.params.get("userId"):
                self.assertEqual(req.url.params["userId"], "7")
                data = {"lessons": [], "stats": {"totalItems": 0}}
            else:
                offset = int(req.url.params["offset"])
                data = {"lessons": [{**self.lesson, "id": 9 + offset}], "stats": {"totalItems": 2}}
        elif path == "subscriptions":
            self.assertEqual(req.url.params["includeFamilySubs"], "true")
            data = {"subscriptions": [{"id": 3, "statusId": 2, "endDate": "2027-04-15"},
                                      {"id": 5, "statusId": 3, "endDate": None}]}
        elif path == "lessons/9":
            self.assertEqual(req.url.params["includeRecords"], "true")
            data = self.lesson
        elif path == "lkSettings":
            data = self.settings
        elif path == "lessonRecords/intersection":
            self.assertEqual(json.loads(req.content), {"userId": 7, "lessonIds": [9]})
            data = self.overlap
        elif path == "lessonRecords/20":
            self.assertEqual(req.method, "DELETE")
            self.writes.append((req.method, path))
            if not self.delay_delete:
                self.lesson["records"] = [r for r in self.lesson["records"] if r["id"] != 20]
            if self.lose_response:
                raise httpx.ReadTimeout("test")
            return httpx.Response(204)
        elif path in ("joins", "lessonRecords"):
            self.writes.append((path, json.loads(req.content)))
            self.lesson["records"] = [{"userId": 7, "lessonId": 9, "id": 20}]
            if self.lose_response:
                raise httpx.ReadTimeout("test")
            return httpx.Response(204)
        else:
            self.fail(path)
        return httpx.Response(200, json=data)

    def test_schedule_and_bookings(self):
        self.assertEqual([x["lesson_id"] for x in self.client.lessons(None, None, 7, False)["lessons"]], [9, 10])
        self.assertEqual(self.client.lessons(None, None, 7, True)["lessons"], [])

    def test_subscription_expiry(self):
        self.assertEqual(self.client.subscriptions(7, False)["subscriptions"][0]["end_date"], "2027-04-15")
        self.assertEqual(len(self.client.subscriptions(7, True)["subscriptions"]), 2)

    def test_existing_join_and_duplicate(self):
        self.assertEqual(self.client.book(9, None, False)["status"], "booked")
        self.assertEqual(self.client.book(9, None, False)["status"], "already_booked")
        self.assertEqual(self.writes, [("lessonRecords", {"userId": 7, "lessonId": 9})])

    def test_new_group(self):
        self.client.account["users"][0]["joins"] = []
        self.assertEqual(self.client.book(9, 7, False)["status"], "booked")
        self.assertEqual(self.writes, [("joins", {"userId": 7, "classId": 4, "lessonIds": [9]})])

    def test_overlap_and_freeze(self):
        self.overlap = [{"lessonId": 55}]
        with self.assertRaisesRegex(ClubError, "пересечение"):
            self.client.book(9, 7, False)
        self.lesson["frozenUsers"] = [7]
        with self.assertRaisesRegex(ClubError, "недоступна"):
            self.client.book(9, 7, True)
        self.assertFalse(self.writes)

    def test_family_requires_profile(self):
        self.client.account["users"].append({"id": 8, "name": "Второй ученик"})
        for student_id in (None, 99):
            with self.assertRaisesRegex(ClubError, "student_id"):
                self.client.book(9, student_id, False)
        self.assertFalse(self.writes)

    def test_lost_response(self):
        self.lose_response = True
        with self.assertRaisesRegex(ClubError, "Проверьте свои записи"):
            self.client.book(9, 7, False)
        self.assertEqual(self.client.book(9, 7, False)["status"], "already_booked")
        self.assertEqual(len(self.writes), 1)

    def test_cancel_and_repeat_keeps_other_students_booking(self):
        sibling = {"id": 21, "userId": 8, "lessonId": 9}
        self.lesson["records"] = [sibling, {"id": 20, "userId": 7, "lessonId": 9}]
        self.assertEqual(self.client.cancel(9, None)["status"], "cancelled")
        self.assertEqual(self.client.cancel(9, None)["status"], "not_booked")
        self.assertEqual(self.lesson["records"], [sibling])
        self.assertEqual(self.writes, [("DELETE", "lessonRecords/20")])

    def test_cancel_requires_identifiable_booking(self):
        record = {"id": 20, "userId": 7, "lessonId": 9}
        for rows in ([record, record], [{**record, "lessonId": 10}], [{**record, "id": None}],
                     [{"id": 20, "lessonId": 9}], None):
            with self.subTest(rows=rows), self.assertRaises(ClubError):
                self.lesson["records"] = rows
                self.client.cancel(9, 7)
        self.assertFalse(self.writes)

    def test_cancel_requires_permitted_mode_and_open_window(self):
        self.lesson["records"] = [{"id": 20, "userId": 7, "lessonId": 9}]
        for mode in ("no", "byClass"):
            self.settings["cancelRecords"] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ClubError, "не разрешает"):
                self.client.cancel(9, 7)
        self.settings["cancelRecords"] = "yes"
        self.settings["cancelRecordsTimeout"] = 60 * 48
        with self.assertRaisesRegex(ClubError, "Срок отмены"):
            self.client.cancel(9, 7)
        self.assertFalse(self.writes)

    def test_unconfirmed_cancellation(self):
        self.lesson["records"] = [{"id": 20, "userId": 7, "lessonId": 9}]
        self.delay_delete = True
        self.assertEqual(self.client.cancel(9, 7)["status"], "submitted_unconfirmed")
        self.assertEqual(len(self.writes), 1)

    def test_cancel_lost_response_does_not_repeat_delete(self):
        self.lesson["records"] = [{"id": 20, "userId": 7, "lessonId": 9}]
        self.lose_response = True
        with self.assertRaisesRegex(ClubError, "Проверьте свои записи"):
            self.client.cancel(9, 7)
        self.assertEqual(self.client.cancel(9, 7)["status"], "not_booked")
        self.assertEqual(len(self.writes), 1)
