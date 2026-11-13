"""MiniMax H3 Image in the agent: two goals of its own, the skill, and the rules that must reach the model.

The model has no negative prompt and names pictures <Picture N>, so the app has to say both things every time:
a negative block would be silently ignored, and <image1> would point at nothing. The skill itself is checked to be
the one the section relies on.

    python tests/unit/test_h3img_agent.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401  (puts app/ on sys.path and a temporary data folder)

import agent as ag

SKILLS = Path(ag.__file__).resolve().parent / "skills"


class H3ImgTargetsTest(unittest.TestCase):
    def test_two_goals_of_its_own(self):
        self.assertEqual(ag.IMAGE_SKILLS["h3t"], "minimax-h3-image")
        self.assertEqual(ag.IMAGE_SKILLS["h3e"], "minimax-h3-image")
        self.assertIn("h3t", ag.AGENT_TARGETS)
        self.assertIn("h3e", ag.AGENT_TARGETS)

    def test_the_skill_is_installed_but_off_by_default(self):
        self.assertTrue((SKILLS / "minimax-h3-image" / "SKILL.md").is_file())
        self.assertEqual(ag.DEFAULT_SKILL_SETTINGS["minimax-h3-image"]["mode"], "off",
                         "скилл подключается своей целью, а не сам по словам")

    def test_the_skill_names_itself(self):
        meta, body = ag.parse_frontmatter((SKILLS / "minimax-h3-image" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("name"), "minimax-h3-image")
        self.assertIn("<Picture 1>", body)
        self.assertIn("негативный промпт не работает", body)
        self.assertIn("16 МП", body)


class H3ImgSkillsTest(unittest.TestCase):
    def setUp(self):
        self.skills = ag.Skills(SKILLS, support.DATA / "no-user-skills", {})

    def ids(self, *args):
        return [s["id"] for s in self.skills.active(*args)]

    def test_a_picture_goal_gets_only_its_own_skill(self):
        self.assertEqual(self.ids("нарисуй кота", "h3t"), ["minimax-h3-image"])
        self.assertEqual(self.ids("нарисуй кота", "h3e"), ["minimax-h3-image"])

    def test_a_storyboard_for_h3img_gets_both_skills(self):
        self.assertEqual(sorted(self.ids("история", "story", "h3img")), ["minimax-h3-image", "storyboard"])

    def test_a_storyboard_for_another_model_does_not(self):
        self.assertEqual(self.ids("история", "story", "flux"), ["storyboard"])
        self.assertEqual(self.ids("история", "story"), ["storyboard"])


class H3ImgPromptTest(unittest.TestCase):
    def prompt(self, target, **ctx):
        return ag.system_prompt("СКИЛЛ", target, ctx)

    def test_the_text_goal_asks_for_one_clean_prompt_without_a_negative(self):
        p = self.prompt("h3t", images=0)
        self.assertIn("MiniMax H3 Image", p)
        self.assertIn("Негативного промпта у", p)
        self.assertIn("16", p)
        self.assertIn("```prompt", p)

    def test_the_edit_goal_names_the_pictures_and_their_roles(self):
        p = self.prompt("h3e", images=3)
        self.assertIn("<Picture 1", p)
        self.assertIn("<Picture 2", p)
        self.assertIn("до 5", p)
        self.assertIn("первая задаёт форму", p)
        self.assertNotIn("КАРТИНОК В ЧАТЕ НЕТ", p)

    def test_without_pictures_the_edit_goal_asks_for_them(self):
        self.assertIn("КАРТИНОК В ЧАТЕ НЕТ", self.prompt("h3e", images=0))

    def test_a_storyboard_by_frames_for_h3img_uses_its_tags(self):
        p = self.prompt("story", model="h3img", frames=True, images=2)
        self.assertIn("MiniMax H3 Image", p)
        self.assertIn("<Picture 1>", p)
        self.assertNotIn('пиши "image 1"', p)
        self.assertIn("МОДЕЛЬ ЭТОГО ЗАПРОСА — MiniMax H3 Image", p)

    def test_a_storyboard_on_one_sheet_for_h3img_uses_its_tags(self):
        p = self.prompt("story", model="h3img", images=2)
        self.assertIn("MiniMax H3 Image", p)
        self.assertIn("<Picture 1>", p)
        self.assertIn("МОДЕЛЬ ЭТОГО ЗАПРОСА — MiniMax H3 Image", p)

    def test_a_sheet_by_description_for_h3img_says_where_the_real_sheet_is(self):
        p = self.prompt("sheet", model="h3img", images=1)
        self.assertIn("МОДЕЛЬ ЭТОГО ЗАПРОСА — MiniMax H3 Image", p)
        self.assertIn("«Лист персонажа»", p)

    def test_other_models_are_not_touched(self):
        self.assertNotIn("МОДЕЛЬ ЭТОГО ЗАПРОСА", self.prompt("story", model="flux", frames=True, images=1))
        self.assertNotIn("<Picture 1", self.prompt("krea", images=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
