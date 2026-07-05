"""v3.39.0: the Django/Jinja template-variable resolver (research/19 — admin's
`is_collapsible`/`inline_formset_data` properties were false-flagged dead
because their only use is `{{ obj.prop }}` in edit_inline templates)."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.model import NodeKind


def test_template_property_reference_rescues_from_dead(tmp_path):
    (tmp_path / "helpers.py").write_text(
        "class InlineFormSet:\n"
        "    @property\n"
        "    def is_collapsible(self):\n"
        "        return self._check_config()\n"
        "    def _check_config(self):\n        return True\n"
        "    def never_rendered(self):\n        return 1\n")
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "tabular.html").write_text(
        "{% if inline_admin_formset.is_collapsible %}<details>{% endif %}\n"
        "<td>{{ forloop.counter }}</td>\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        tids = [n.id for n in store.nodes_by_kind(NodeKind.TEMPLATE)]
        assert any("tabular.html" in t for t in tids)
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "InlineFormSet.is_collapsible" not in stale   # template-rescued
        assert "InlineFormSet._check_config" not in stale    # through the property
        assert "InlineFormSet.never_rendered" in stale       # still honest


def test_stoplist_and_bare_names_add_nothing(tmp_path):
    """Ubiquitous segments (items/count/...) and un-dotted names must not fan."""
    (tmp_path / "m.py").write_text(
        "class Bag:\n"
        "    def items(self):\n        return []\n"
        "    def count(self):\n        return 0\n")
    tpl = tmp_path / "t"
    tpl.mkdir()
    (tpl / "page.html").write_text(
        "{% for k, v in bag.items %}{{ v }}{% endfor %} {{ bag.count }} {{ solo }}\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        n = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src LIKE '%::template'").fetchone()[0]
        assert n == 0


def test_plain_html_without_tags_is_ignored(tmp_path):
    (tmp_path / "m.py").write_text("def render_page():\n    return 1\n")
    (tmp_path / "static.html").write_text("<html><body>render_page</body></html>\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert store.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE id LIKE 'static.html%'").fetchone()[0] == 0
