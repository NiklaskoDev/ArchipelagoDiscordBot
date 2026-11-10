import ast
from collections import Counter

from apbot.ui import style


def test_no_style_constant_is_defined_twice():
    tree = ast.parse(open(style.__file__, encoding="utf-8").read())
    names = [t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)]
    assert [name for name, count in Counter(names).items() if count > 1] == []
