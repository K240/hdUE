"""Checks that public files do not depend on a private workstation layout."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
MACHINE_PATH=re.compile(r'(?<![\w])[A-Za-z]:[\\/]')
CJK=re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')


def candidates():
    result=subprocess.check_output(['git','ls-files','-c','-o','--exclude-standard','-z'],cwd=ROOT)
    return sorted(set(name for name in result.decode().split('\0') if name and (ROOT/name).is_file()))


class RepositoryScope(unittest.TestCase):
    def test_no_machine_paths_or_external_symlinks(self):
        violations=[]
        for name in candidates():
            file=ROOT/name
            if not file.resolve().is_relative_to(ROOT.resolve()):
                violations.append(name+' (external symlink)'); continue
            try: text=file.read_text(encoding='utf-8-sig')
            except UnicodeError: continue
            if MACHINE_PATH.search(text): violations.append(name)
        self.assertEqual(violations,[])

    def test_documentation_is_english(self):
        violations=[name for name in candidates() if name.endswith('.md') and CJK.search((ROOT/name).read_text(encoding='utf-8-sig'))]
        self.assertEqual(violations,[])

    def test_private_content_is_not_a_public_candidate(self):
        violations=[]
        for name in candidates():
            path=Path(name)
            if path.parts[0] in ('.local','out','build','external','projects') or 'Content' in path.parts:
                violations.append(name)
            if path.suffix=='.uproject' and name!='unreal/HdUEBackend/HdUEBackend.uproject':
                violations.append(name)
        self.assertEqual(violations,[])
        owned='unreal/HdUEBackend/HdUEBackend.uproject'
        self.assertIn(owned,candidates())


if __name__=='__main__': unittest.main()
