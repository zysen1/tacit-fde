"""Single-file offline HTML report. No CDN, no build step."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tacit · {{ company }}</title>
<style>
  :root {
    --paper: #f3efe4;
    --ink: #1c1915;
    --muted: #5c564c;
    --line: #ddd4c4;
    --card: #fffdf8;
    --green: #0f6e56;
    --green-soft: #e5f4ef;
    --rust: #9a3412;
    --rust-soft: #fde8df;
    --gold: #8a5a00;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font: 16px/1.5 "Iowan Old Style", Palatino, "Palatino Linotype", "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", "WenQuanYi Micro Hei", Georgia, serif;
  }
  header {
    padding: 28px 7vw 18px;
    border-bottom: 1px solid var(--line);
    display: flex;
    justify-content: space-between;
    gap: 24px;
    align-items: end;
  }
  .mark { font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, "WenQuanYi Micro Hei Mono", monospace; letter-spacing: 0.14em; font-size: 13px; color: var(--green); }
  h1 { font-size: 34px; line-height: 1.1; margin: 6px 0; font-weight: 600; }
  h2 { font-size: 20px; margin: 0 0 12px; }
  .sub { color: var(--muted); max-width: 62ch; }
  main { padding: 22px 7vw 64px; display: grid; gap: 18px; }
  .grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 18px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip { font-family: ui-monospace, Menlo, Consolas, "WenQuanYi Micro Hei Mono", monospace; font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; background: #fff; }
  .rule { border-top: 1px solid var(--line); padding: 12px 0; }
  .rule:first-of-type { border-top: 0; }
  .stmt { font-size: 17px; }
  .meta { color: var(--muted); font-size: 13px; font-family: ui-monospace, Menlo, Consolas, "WenQuanYi Micro Hei Mono", monospace; }
  .ev { margin: 4px 0; font-size: 14px; }
  .tag { display: inline-block; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; padding: 1px 6px; border-radius: 999px; background: var(--green-soft); color: var(--green); margin-right: 4px; }
  .tag.conflict { background: var(--rust-soft); color: var(--rust); }
  .tag.explicit { background: #fff4d6; color: var(--gold); }
  .banner { background: var(--rust-soft); border: 1px solid #f0c7b6; border-radius: 14px; padding: 14px 16px; }
  .banner strong { color: var(--rust); }
  .answer { margin-top: 8px; }
  .bar-row { display: grid; grid-template-columns: 110px 1fr 64px; gap: 8px; align-items: center; margin: 6px 0; font-size: 14px; }
  .track { background: #efe8da; border-radius: 999px; height: 12px; overflow: hidden; }
  .fill { height: 100%; background: var(--green); }
  .fill.bad { background: var(--rust); }
  svg { width: 100%; height: auto; }
  footer { color: var(--muted); font-size: 13px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } h1 { font-size: 28px; } }
</style>
</head>
<body>
<header>
  <div>
    <div class="mark">TACIT · OFFLINE REPORT</div>
    <h1>{{ company }}</h1>
    <div class="sub">{{ headline }}</div>
  </div>
  <div class="meta">seed {{ seed }} · {{ n_files }} files · {{ n_rules }} rules</div>
</header>
<main>
  <section class="grid">
    <article class="card">
      <h2>Estate</h2>
      <div class="chips">
        {% for item in inventory %}
        <span class="chip">{{ item.kind }} · {{ item.path }}</span>
        {% endfor %}
      </div>
    </article>
    <article class="card">
      <h2>Ablation on held-out cases</h2>
      {% if ablation %}
      <div class="meta">success rate</div>
      {% for row in success_bars %}
      <div class="bar-row"><span>{{ row.name }}</span><div class="track"><div class="fill" style="width: {{ row.width }}"></div></div><span>{{ row.label }}</span></div>
      {% endfor %}
      <div class="meta" style="margin-top:10px">violation rate (too permissive)</div>
      {% for row in violation_bars %}
      <div class="bar-row"><span>{{ row.name }}</span><div class="track"><div class="fill bad" style="width: {{ row.width }}"></div></div><span>{{ row.label }}</span></div>
      {% endfor %}
      {% else %}
      <p>No ground truth was attached, so this run has no ablation.</p>
      {% endif %}
    </article>
  </section>

  {% if conflicts %}
  <section class="banner">
    <strong>Conflict.</strong>
    {% for question in conflicts %}
    <div>{{ question.prompt }}</div>
    {% if question.answer %}<div class="answer"><strong>Simulated customer:</strong> {{ question.answer }}</div>{% endif %}
    {% endfor %}
  </section>
  {% endif %}

  <section class="card">
    <h2>Rules and evidence</h2>
    <svg viewBox="0 0 760  {{ graph_h }}" role="img" aria-label="Rules linked to evidence files">
      {% for edge in edges %}
      <line x1="250" y1="{{ edge.y1 }}" x2="470" y2="{{ edge.y2 }}" stroke="#c8beae" stroke-width="1.4"/>
      {% endfor %}
      {% for node in rule_nodes %}
      <rect x="16" y="{{ node.y }}" width="230" height="26" rx="6" fill="#e5f4ef" stroke="#0f6e56"/>
      <text x="26" y="{{ node.y + 17 }}" font-size="12" font-family="ui-monospace, Menlo, WenQuanYi Micro Hei Mono, monospace" fill="#1c1915">{{ node.label }}</text>
      {% endfor %}
      {% for node in file_nodes %}
      <rect x="474" y="{{ node.y }}" width="270" height="26" rx="6" fill="#fffdf8" stroke="#ddd4c4"/>
      <text x="484" y="{{ node.y + 17 }}" font-size="12" font-family="ui-monospace, Menlo, WenQuanYi Micro Hei Mono, monospace" fill="#1c1915">{{ node.label }}</text>
      {% endfor %}
    </svg>
    {% for rule in rules %}
    <div class="rule">
      <div class="stmt">{{ rule.statement }}</div>
      <div class="meta">
        <span class="tag {{ rule.tag }}">{{ rule.status }}</span>
        {% if rule.explicit %}<span class="tag explicit">explicit</span>{% endif %}
        {% if rule.conflict_group %}<span class="tag conflict">conflict {{ rule.conflict_group }}</span>{% endif %}
        confidence {{ rule.confidence }} · {{ rule.kinds }}
      </div>
      {% for source in rule.sources %}
      <div class="ev"><span class="tag">{{ source.kind }}</span> {{ source.where }} — {{ source.detail }}</div>
      {% endfor %}
    </div>
    {% endfor %}
  </section>
  <footer>
    Discovery recall {{ recall }} · precision {{ precision }}.
    Questions per confirmed tacit rule: {{ qper }}.
    This page is static and offline. Numbers are from this run, not from a target score.
  </footer>
</main>
</body>
</html>
"""


def _bars(ablation: dict[str, Any] | None, key: str) -> list[dict[str, str]]:
    if not ablation:
        return []
    order = [("none", "No rules"), ("explicit", "Explicit only"), ("tacit", "Plus tacit")]
    rows = []
    for name, label in order:
        value = float(ablation[name][key])
        rows.append({"name": label, "width": f"{max(0.0, min(value, 1.0)) * 100:.1f}%", "label": f"{value:.1%}"})
    return rows


def _short(text: str, limit: int = 42) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_report(payload: dict[str, Any]) -> str:
    rules_out = []
    file_index: dict[str, int] = {}
    edges = []
    rule_nodes = []
    for rule in payload["rules"]:
        sources = []
        for source in rule.get("sources") or []:
            where = source.get("path") or source.get("table") or ""
            if source.get("line"):
                where = f"{where}:{source['line']}"
            detail = " ".join(str(source.get("snippet") or "").split())
            if len(detail) > 180:
                detail = detail[:177] + "..."
            sources.append({"kind": source.get("kind"), "where": where, "detail": detail})
            file_index.setdefault(where.split(":")[0] or where, len(file_index))
        tag = "conflict" if rule.get("conflict_group") else ("explicit" if rule.get("explicit") else "")
        rules_out.append(
            {
                "statement": rule.get("statement"),
                "status": rule.get("status"),
                "explicit": rule.get("explicit"),
                "conflict_group": rule.get("conflict_group"),
                "confidence": rule.get("confidence"),
                "kinds": ", ".join(rule.get("source_kinds") or []),
                "sources": sources[:6],
                "tag": tag,
            }
        )
    shown = rules_out[:12]
    for index, rule in enumerate(shown):
        y = 10 + index * 34
        rule_nodes.append({"y": y, "label": _short(rule["statement"], 32)})
        for source in rule["sources"][:1]:
            path = source["where"].split(":")[0]
            edges.append({"y1": y + 13, "y2": 10 + file_index.get(path, 0) * 34 + 13})
    file_nodes = []
    for path, index in list(file_index.items())[:12]:
        file_nodes.append({"y": 10 + index * 34, "label": _short(path, 36)})
    graph_h = 20 + max(len(rule_nodes), len(file_nodes), 1) * 34
    metrics = payload.get("metrics") or {}
    ranking = (metrics.get("clarification") or {}).get("ranking") or {}
    env = Environment(autoescape=True)
    template = env.from_string(_HTML)
    return template.render(
        company=payload["company"],
        headline=payload["headline"],
        seed=payload.get("seed"),
        n_files=len(payload["inventory"]),
        n_rules=len(payload["rules"]),
        inventory=payload["inventory"],
        ablation=metrics.get("ablation"),
        success_bars=_bars(metrics.get("ablation"), "success_rate"),
        violation_bars=_bars(metrics.get("ablation"), "violation_rate"),
        conflicts=payload.get("conflicts") or [],
        rules=rules_out,
        rule_nodes=rule_nodes,
        file_nodes=file_nodes,
        edges=edges,
        graph_h=graph_h,
        recall=metrics.get("tacit_recall"),
        precision=metrics.get("precision"),
        qper=ranking.get("questions_per_confirmed_rule"),
    )


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(render_report(payload), encoding="utf-8")
