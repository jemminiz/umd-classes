import unittest

from umdsched.sources import umdio


class ParseUmdIo(unittest.TestCase):
    def test_section(self):
        raw = {
            "section_id": "CMSC216-0101",
            "course": "CMSC216",
            "instructors": ["Example Instructor"],
            "seats": "36",
            "open_seats": "0",
            "waitlist": "12",
            "meetings": [
                {"days": "MWF", "room": "0324", "building": "IRB", "classtype": "", "start_time": "10:00am", "end_time": "10:50am"},
                {"days": "Tu", "room": "2117", "building": "CSI", "classtype": "Discussion", "start_time": "9:00am", "end_time": "9:50am"},
            ],
        }
        sec = umdio.parse_section("CMSC216", raw)
        self.assertEqual(sec.section_id, "0101")
        self.assertEqual((sec.seats_total, sec.seats_open, sec.waitlist), (36, 0, 12))
        self.assertEqual(sec.meetings[0].kind, "Lecture")
        self.assertEqual(sec.meetings[1].kind, "Discussion")
        self.assertEqual(sec.meetings[1].start, 540)

    def test_online_async(self):
        raw = {"section_id": "ENGL101-ESG1", "instructors": ["TBA"], "meetings": [{"days": "", "room": "ONLINE", "building": "", "start_time": "", "end_time": ""}]}
        sec = umdio.parse_section("ENGL101", raw)
        self.assertEqual(sec.instructors, [])
        self.assertEqual(sec.delivery, "online")
        self.assertFalse(sec.meetings[0].timed)


if __name__ == "__main__":
    unittest.main()
