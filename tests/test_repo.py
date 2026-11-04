import json
import os
import re
import unittest

from _load import ROOT

# v2, the day-trading pack: the eleven folders the reel's repo card shows
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-deals"]
ABOUT = ("11 Claude skills for day trading the Indian market (NSE): scan thousands of charts, track breaking "
         "news, time every entry and exit, and replay every outcome before you risk a rupee.")
CONTACT = "amansoni9399@gmail.com"
NOTE = "For now this is paper trading; to use it with real money, contact " + CONTACT + "."
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-deals", "trader-risk", "trader-broker", "trader-alerts"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]
# working folders that .gitignore keeps out of git: the checks below skip them
LOCAL_ONLY = (".venv", "venv", "trader", "__pycache__")
REPO_URL = "https://github.com/amansoni7477030/day-trader-skill-indian-market"
AUTHOR = {"name": "Aman Soni", "url": "https://toolrux.com"}


def read_json(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def read_text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d != ".git" and d not in LOCAL_ONLY]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d not in LOCAL_ONLY)
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "requirements.txt", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("an order is placed only when trader-risk passed that exact order today", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        plugin = read_json(".claude-plugin", "plugin.json")
        self.assertEqual(plugin["name"], "day-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT + " " + NOTE)
        market = read_json(".claude-plugin", "marketplace.json")
        self.assertEqual(market["name"], "day-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill")

    def test_readme_carries_the_reel_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill"))
        for line in (ABOUT,
                     "https://github.com/amansoni7477030/day-trader-skill-indian-market install skill",
                     "/plugin marketplace add amansoni7477030/day-trader-skill-indian-market",
                     "/plugin install day-trader-skill@day-trader-skill",
                     "## Fine print"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue  # the README points v1 users to the v1 commit
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)

    def test_the_account_starts_with_one_lakh_and_there_is_no_mode_to_switch(self):
        cfg = read_json("trader-budget", "config.example.json")
        self.assertEqual(cfg["budget"], 100000)  # the account starts with one lakh rupees
        for gone in ("broker", "paper"):
            self.assertNotIn(gone, cfg)
        self.assertIs(cfg.get("allow_options", False), False)

    def test_the_skills_do_not_talk_about_paper_trading_or_ask_for_a_yes(self):
        # an order that passes trader-risk goes through on its own, and the skills name no "paper" mode
        for s in SKILLS:
            low = " ".join(read_text(s, "SKILL.md").lower().split())
            for bad in ("paper", "your yes", "explicit yes", "yes or no", "say yes"):
                self.assertNotIn(bad, low, "%s says %r" % (s, bad))

    def test_author_and_website_are_aman_soni_and_toolrux(self):
        plugin = read_json(".claude-plugin", "plugin.json")
        market = read_json(".claude-plugin", "marketplace.json")
        self.assertEqual(plugin["author"], AUTHOR)
        self.assertEqual(market["owner"], AUTHOR)
        self.assertEqual(plugin["homepage"], REPO_URL)
        self.assertEqual(plugin["repository"], REPO_URL)
        self.assertEqual(plugin["license"], "MIT")
        self.assertFalse([k for k in plugin["keywords"] if "paper" in k or "dry-run" in k])

    def test_the_descriptions_say_paper_trading_for_now_and_give_the_real_money_contact(self):
        plugin = read_json(".claude-plugin", "plugin.json")
        market = read_json(".claude-plugin", "marketplace.json")
        for text in (plugin["description"], market["description"], market["plugins"][0]["description"]):
            self.assertTrue(text.endswith(NOTE), text)
        readme = " ".join(read_text("README.md").split())
        self.assertIn("For now this is paper trading.", readme)
        self.assertIn("[%s](mailto:%s)" % (CONTACT, CONTACT), readme)

    def test_license_keeps_the_original_notice(self):
        text = read_text("LICENSE")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Jake Schincariol", text)  # the MIT license requires the original notice to stay

    def test_requirements_list_only_curl_cffi(self):
        lines = [ln.strip() for ln in read_text("requirements.txt").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^curl_cffi\s*(>=|==|~=)\s*\d")

    def test_gitignore_keeps_local_files_out_of_git(self):
        lines = [ln.strip() for ln in read_text(".gitignore").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        for pattern in (".venv/", "__pycache__/", "/trader/", ".env"):
            self.assertIn(pattern, lines, pattern)

    def test_no_tool_reads_the_environment_so_there_is_no_env_example(self):
        # no key, token or password anywhere: if a tool ever needs one, add .env.example and say so in the README
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if f.endswith(".py"):
                    src = read_text(s, f)
                    for bad in ("os.environ", "os.getenv", "dotenv"):
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_readme_has_the_credits_the_install_steps_and_architecture_diagrams(self):
        readme = read_text("README.md")
        low = " ".join(readme.lower().split())
        for text in ("aman soni", "https://toolrux.com", "jake schincariol", "nothing to confirm",
                     "\u20b91,00,000", "python3 -m venv .venv", "pip install -r requirements.txt"):
            self.assertIn(text, low, text)
        for gone in ("Kite", "Allow once", "Needs approval", "Always allow", "static IP", "Going live"):
            self.assertNotIn(gone, readme, gone)
        blocks = re.findall(r"```mermaid\n(.*?)\n```", readme, re.S)
        self.assertEqual(readme.count("```mermaid"), len(blocks))
        kinds = [b.split(None, 1)[0] for b in blocks]
        for kind in ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gantt"):
            self.assertIn(kind, kinds)


if __name__ == "__main__":
    unittest.main()
