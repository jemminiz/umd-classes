import unittest
from pathlib import Path

from umdsched.models import ONLINE
from umdsched.sources import testudo

FIXTURES = Path(__file__).parent / "fixtures"


class ParseSearchPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.courses = {c.course_id: c for c in testudo.parse_courses((FIXTURES / "testudo_search.html").read_text(encoding="utf-8"))}

    def test_courses_and_titles(self):
        self.assertEqual(set(self.courses), {"CMSC216", "CMSC216H"})
        c = self.courses["CMSC216"]
        self.assertEqual(c.title, "Introduction to Computer Systems")
        self.assertEqual(c.credits, 4)
        self.assertEqual(self.courses["CMSC216H"].sections, [])

    def test_lecture_and_discussion(self):
        sec = self.courses["CMSC216"].sections[0]
        self.assertEqual(sec.section_id, "0101")
        self.assertEqual(sec.instructors, ["Example Instructor"])
        self.assertEqual((sec.seats_total, sec.seats_open, sec.waitlist, sec.holdfile), (36, 0, 12, 2))
        self.assertFalse(sec.is_open)
        lec, dis = sec.meetings
        self.assertEqual((lec.days, lec.start, lec.end, lec.building, lec.room), ((0, 2, 4), 600, 650, "IRB", "0324"))
        self.assertEqual(lec.kind, "Lecture")
        self.assertEqual((dis.days, dis.start, dis.building, dis.kind), ((1,), 540, "CSI", "Discussion"))

    def test_multiple_instructors_blended_and_online_meeting(self):
        sec = self.courses["CMSC216"].sections[1]
        self.assertEqual(sec.instructors, ["Second Teacher", "Third Teacher"])
        self.assertEqual(sec.delivery, "blended")
        self.assertEqual(sec.meetings[0].start, 14 * 60)
        online = sec.meetings[1]
        self.assertEqual(online.building, ONLINE)
        self.assertTrue(online.timed and online.online and not online.in_person)

    def test_async_online_section(self):
        sec = self.courses["CMSC216"].sections[2]
        self.assertEqual(sec.section_id, "ESG1")
        self.assertEqual(sec.instructors, [])
        self.assertEqual(sec.delivery, "online")
        self.assertEqual(len(sec.meetings), 1)
        self.assertFalse(sec.meetings[0].timed)
        self.assertTrue(sec.meetings[0].online)


class ParseSectionsEndpoint(unittest.TestCase):
    def test_sections_fragment(self):
        by_course = testudo.parse_sections_page((FIXTURES / "testudo_sections.html").read_text(encoding="utf-8"))
        (sec,) = by_course["MATH240"]
        self.assertEqual(sec.section_id, "0111")
        self.assertEqual((sec.seats_total, sec.seats_open, sec.waitlist), (30, 4, 0))
        self.assertEqual([m.building for m in sec.meetings], ["MTH", "PHY"])
        self.assertEqual(sec.meetings[0].start, 8 * 60)


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.pages[url.rsplit("/", 1)[-1]])


class FetchCourse(unittest.TestCase):
    def test_falls_back_to_sections_endpoint(self):
        search = '<div class="course" id="MATH240"><div class="course-id">MATH240</div><span class="course-title">Linear Algebra</span><span class="course-min-credits">4</span></div>'
        sess = FakeSession({"search": search, "sections": (FIXTURES / "testudo_sections.html").read_text()})
        course = testudo.fetch_course("math240", "202701", session=sess)
        self.assertEqual(course.course_id, "MATH240")
        self.assertEqual(len(course.sections), 1)
        self.assertEqual(sess.calls[0][1]["courseId"], "math240")
        self.assertEqual(sess.calls[0][1]["termId"], "202701")
        self.assertEqual(sess.calls[1][0], f"{testudo.BASE_URL}/202701/sections")

    def test_missing_course(self):
        sess = FakeSession({"search": "<p>No courses matched your search filters above.</p>"})
        self.assertIsNone(testudo.fetch_course("CMSC999", "202701", session=sess))


if __name__ == "__main__":
    unittest.main()
