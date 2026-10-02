"""Distribution guards: neutral startup, no owner data or tracked runtime secrets."""
from pathlib import Path
import json
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
RUNTIME_DIRS = {'.crewbot', 'team', 'runs', 'companies', '__pycache__'}
RUNTIME_FILES = {'company-state.json', 'dashboard-state.json', 'state.json', 'company-brief.txt'}
TEXT_SUFFIXES = {'.py', '.js', '.html', '.css', '.md', '.json', '.txt', '.ps1', '.svg', '.yml', '.yaml'}


class CrewBotReleaseTests(unittest.TestCase):
    def test_distribution_starts_with_blank_business_and_no_employees(self):
        company = json.loads((ROOT / 'company.json').read_text(encoding='utf-8-sig'))
        for field in ('name', 'positioning', 'summary', 'market', 'business_model', 'goals', 'operating_rules'):
            self.assertTrue(company[field] == '', 'Shipped business field must be blank: ' + field)
        self.assertEqual(company['services'], [])
        self.assertEqual(company['portfolio'], [])
        self.assertEqual(json.loads((ROOT / 'employees.json').read_text(encoding='utf-8-sig')), [])

    def test_env_example_contains_names_but_no_values(self):
        expected = {'OPENAI_API_KEY', 'OPENAI_PROJECT_ID', 'OPENROUTER_API_KEY', 'TAVILY_API_KEY',
                    'FIRECRAWL_API_KEY', 'RESEND_API_KEY', 'SLACK_BOT_TOKEN', 'GITHUB_TOKEN'}
        variables = {}
        for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'): continue
            name, separator, value = line.partition('=')
            self.assertTrue(separator, 'Invalid environment example line')
            self.assertTrue(value.strip() == '', 'Environment examples must not include credentials or project values')
            variables[name] = value
        self.assertEqual(set(variables), expected)

    def test_production_distribution_is_owner_neutral(self):
        old_brand = ''.join(('Kan', 'youAI'))
        private_patterns = [re.compile(re.escape(old_brand), re.I),
                            re.compile(r'proj_[A-Za-z0-9]{20,}'),
                            re.compile(r'\b(?:sk-(?:proj-|or-v1-)?|github_pat_)[A-Za-z0-9_-]{35,}')]
        for path in ROOT.rglob('*'):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES: continue
            if any(part in RUNTIME_DIRS or part == '.git' for part in relative.parts): continue
            if path.name.startswith('test_'): continue
            if path.name in RUNTIME_FILES or path.name.startswith('credentials'): continue
            content = path.read_text(encoding='utf-8-sig', errors='replace')
            for pattern in private_patterns:
                self.assertFalse(bool(pattern.search(content)), 'Owner data or credential found in ' + relative.as_posix())

    def test_git_does_not_track_runtime_or_credentials(self):
        try:
            top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, capture_output=True, text=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest('Source archive is not a Git checkout')
        if Path(top.stdout.strip()).resolve() != ROOT.resolve():
            self.skipTest('CrewBot is not the checkout root')
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\0')
        for name in filter(None, tracked):
            path = Path(name)
            self.assertFalse(any(part in RUNTIME_DIRS for part in path.parts), 'Runtime folder tracked: ' + name)
            self.assertNotIn(path.name, RUNTIME_FILES, 'Runtime state tracked: ' + name)
            self.assertFalse(path.name.startswith('credentials'), 'Credential file tracked: ' + name)
            self.assertFalse(path.name.startswith('.env') and path.name != '.env.example', 'Environment file tracked: ' + name)
            self.assertNotIn(path.suffix.lower(), ('.zip', '.pyc'), 'Generated archive/bytecode tracked: ' + name)


if __name__ == '__main__': unittest.main()
