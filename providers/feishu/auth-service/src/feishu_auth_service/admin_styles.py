from __future__ import annotations


CONTROL_ROOM_STYLES = """
:root {
  --ink: #172033;
  --ink-strong: #0d1424;
  --muted: #66758d;
  --canvas: #f4f7fb;
  --surface: #ffffff;
  --surface-subtle: #f8faff;
  --line: #dce4ef;
  --line-strong: #c8d3e2;
  --blue: #2463e8;
  --blue-hover: #1d4fbd;
  --blue-soft: #edf3ff;
  --green: #15866d;
  --green-soft: #eaf7f3;
  --red: #b9381f;
  --red-soft: #fff1ed;
  --rail: #101827;
  --rail-muted: #8d9cb2;
  --content-width: 1040px;
  --shadow: 0 20px 60px rgba(18, 35, 63, .08), 0 2px 8px rgba(18, 35, 63, .04);
  font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", "PingFang SC", sans-serif;
  color: var(--ink);
  background: var(--canvas);
}

body {
  background: var(--canvas);
}

.app-frame {
  grid-template-columns: 232px minmax(0, 1fr);
}

.rail {
  padding: 26px 24px;
  background: var(--rail);
  border-right: 1px solid rgba(255, 255, 255, .06);
}

.wordmark {
  gap: 11px;
  font-size: 12px;
  letter-spacing: .025em;
}

.wordmark > span {
  width: 40px;
  height: 40px;
  border-color: rgba(255, 255, 255, .28);
  border-radius: 8px;
  background: rgba(255, 255, 255, .035);
  font-size: 15px;
}

.rail-nav {
  margin-top: 58px;
}

.rail ol {
  margin: 0;
}

.rail li {
  gap: 15px;
  margin-bottom: 28px;
  color: #718096;
}

.rail li strong {
  font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", sans-serif;
  font-size: 13px;
  letter-spacing: .01em;
}

.rail li small {
  margin-top: 4px;
  font-size: 11px;
}

.rail li.complete {
  color: var(--rail-muted);
}

.rail li.complete .rail-node {
  border-color: #6d91dd;
  background: #6d91dd;
}

.rail li.active .rail-node {
  border-width: 3px;
  background: var(--blue);
  box-shadow: 0 0 0 5px var(--rail), 0 0 0 7px rgba(88, 137, 235, .4);
}

.binding-badge {
  padding: 16px 12px 0;
  color: #aab7ca;
  font-size: 11px;
}

main {
  width: 100%;
  min-width: 0;
  padding: 48px clamp(36px, 5vw, 88px) 26px;
  background-color: var(--canvas);
  background-image:
    linear-gradient(rgba(36, 99, 232, .025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(36, 99, 232, .025) 1px, transparent 1px);
  background-size: 32px 32px;
}

.page {
  width: min(var(--content-width), 100%);
  min-height: calc(100vh - 74px);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}

.hero {
  margin-bottom: 26px;
}

.hero.compact {
  max-width: 900px;
}

.portal-hero {
  max-width: none !important;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 250px;
  gap: 56px;
  align-items: end;
}

.eyebrow {
  margin-bottom: 9px;
  color: var(--blue);
  font-size: 10px;
  letter-spacing: .17em;
}

.hero h1,
.result-block h1,
.hero.compact h1 {
  max-width: 820px;
  font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", "PingFang SC", sans-serif;
  font-size: clamp(34px, 4vw, 48px);
  font-weight: 720;
  line-height: 1.08;
  letter-spacing: -.035em;
}

.portal-hero h1 {
  font-size: clamp(38px, 4.2vw, 52px);
}

.lede {
  max-width: 680px;
  margin-top: 14px;
  font-size: 16px;
  line-height: 1.65;
}

.session-note {
  padding: 4px 0 4px 22px;
  border-left: 2px solid var(--blue);
}

.session-note-label {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--blue);
  font: 700 10px/1.2 Consolas, "Cascadia Mono", monospace;
  letter-spacing: .12em;
  text-transform: uppercase;
}

.session-note-label i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 0 4px var(--green-soft);
}

.session-note strong,
.session-note small {
  display: block;
}

.session-note strong {
  margin-top: 12px;
  font-size: 14px;
}

.session-note small {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}

.provider-card,
.panel,
.danger-zone,
.result-block {
  border-color: var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow);
  overflow: hidden;
}

.provider-card {
  padding: 0;
}

.provider-head {
  align-items: center;
  padding: 25px 30px;
  background: var(--surface);
}

.provider-identity {
  display: flex;
  align-items: center;
  gap: 16px;
}

.provider-symbol {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border: 1px solid #ccdaf7;
  border-radius: 12px;
  color: var(--blue);
  background: var(--blue-soft);
  font: 750 13px/1 Bahnschrift, "Segoe UI Variable Display", sans-serif;
  letter-spacing: .08em;
}

.provider-head .eyebrow {
  margin-bottom: 5px;
}

h2 {
  font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif;
  font-size: 21px;
}

.provider-caption {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.status {
  flex: 0 0 auto;
  padding: 7px 11px;
  border-radius: 999px;
}

.provider-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  padding: 6px 30px 0;
}

.provider-facts > div {
  display: block;
  min-width: 0;
  margin: 16px 22px 16px 0;
  padding: 4px 22px 4px 0;
  border-right: 1px solid var(--line);
  border-bottom: 0;
}

.provider-facts > div:last-child {
  margin-right: 0;
  padding-right: 0;
  border-right: 0;
}

.provider-facts dt {
  font-size: 11px;
  font-weight: 650;
}

.provider-facts dd {
  margin-top: 9px;
  font-size: 14px;
  overflow-wrap: anywhere;
}

.provider-empty {
  padding: 28px 30px;
}

.provider-empty p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.provider-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 19px 30px;
  border-top: 1px solid var(--line);
  background: var(--surface-subtle);
}

.provider-actions .session-exit {
  margin: 0;
  text-align: left;
}

.button {
  min-height: 42px;
  gap: 10px;
  padding: 0 18px;
  border-radius: 8px;
  font-size: 14px;
  transition: background-color .16s ease, border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}

.button.primary {
  background: var(--blue);
  box-shadow: 0 6px 16px rgba(36, 99, 232, .18);
}

.button.primary:hover {
  background: var(--blue-hover);
  transform: translateY(-1px);
}

.button.quiet:hover {
  border-color: #aab9ce;
  background: var(--surface-subtle);
}

.panel {
  padding: 32px;
}

.readonly,
.secret-state,
.validation-stamp {
  border-radius: 10px;
}

.field input,
.delete-confirm input {
  border-radius: 8px;
}

.danger-zone {
  box-shadow: none;
}

.session-exit {
  margin-top: 18px;
}

.text-button {
  padding: 7px 0;
  font-size: 13px;
  text-decoration-thickness: 1px;
}

.result-block {
  max-width: 820px;
}

footer {
  margin-top: auto;
  padding-top: 18px;
  color: #7b899e;
}

main > * {
  animation: none;
}

.page > * {
  animation: enter .28s ease-out both;
}

@media (prefers-reduced-motion: reduce) {
  .page > * {
    animation: none;
  }

  .button {
    transition: none;
  }
}

@media (max-width: 1080px) {
  .app-frame {
    grid-template-columns: 210px minmax(0, 1fr);
  }

  .rail {
    padding-inline: 21px;
  }

  main {
    padding: 42px 36px 24px;
  }

  .portal-hero {
    gap: 32px;
  }
}

@media (max-width: 820px) {
  .app-frame {
    display: block;
  }

  .rail {
    position: relative;
    height: auto;
    padding: 18px 20px;
  }

  .wordmark {
    margin-bottom: 20px;
  }

  .rail-nav {
    margin-top: 0;
  }

  .rail ol {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
  }

  .rail ol::before {
    left: 8%;
    right: 8%;
    top: 8px;
    bottom: auto;
    width: auto;
    height: 1px;
  }

  .rail li {
    display: block;
    margin: 0;
    text-align: center;
  }

  .rail-node {
    display: block;
    margin: 0 auto 9px;
  }

  .binding-badge {
    display: none;
  }

  main {
    padding: 34px 24px 22px;
  }

  .page {
    min-height: auto;
  }

  .portal-hero {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .session-note {
    max-width: 360px;
  }

  .provider-facts {
    grid-template-columns: 1fr;
  }

  .provider-facts > div,
  .provider-facts > div:last-child {
    margin: 0;
    padding: 17px 0;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .provider-facts > div:last-child {
    border-bottom: 0;
  }
}

@media (max-width: 560px) {
  main {
    padding-inline: 16px;
  }

  .hero h1,
  .hero.compact h1,
  .portal-hero h1,
  .result-block h1 {
    font-size: 34px;
  }

  .provider-head,
  .provider-actions,
  .danger-zone,
  .secret-state {
    align-items: stretch;
    flex-direction: column;
  }

  .provider-head {
    padding: 22px;
  }

  .status {
    align-self: flex-start;
  }

  .provider-facts {
    padding-inline: 22px;
  }

  .provider-actions {
    flex-direction: column-reverse;
    padding: 18px 22px;
  }

  .provider-actions .session-exit,
  .provider-actions .button,
  .provider-actions .text-button {
    width: 100%;
  }

  .panel,
  .result-block {
    padding: 24px;
  }
}
"""
