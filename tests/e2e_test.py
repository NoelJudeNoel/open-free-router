#!/usr/bin/env python3
"""Full end-to-end agent connectivity test."""
import json, yaml, urllib.request, subprocess, sys, time

PASS = 0
FAIL = 0
WARN = 0

def p(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")

def f(msg, detail=""):
    global FAIL
    FAIL += 1
    d = f" — {detail[:80]}" if detail else ""
    print(f"  ❌ {msg}{d}")

def w(msg, detail=""):
    global WARN
    WARN += 1
    d = f" — {detail[:80]}" if detail else ""
    print(f"  ⚠️  {msg}{d}")

def proxy_call(model, timeout=15):
    data = json.dumps({"model": model, "messages": [{"role": "user", "content": "OK"}], "max_tokens": 5}).encode()
    req = urllib.request.Request("http://127.0.0.1:8337/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
            if "choices" in body:
                return (True, body["choices"][0]["message"]["content"][:40])
            return (False, str(body.get("error",""))[:60])
    except urllib.request.HTTPError as e:
        err = e.read().decode()[:80] if hasattr(e, 'read') else str(e)[:80]
        return (False, f"HTTP {e.code}: {err}")
    except Exception as e:
        return (False, str(e)[:80])

def http_get(path, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:8337{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (True, r.status, r.read())
    except urllib.request.HTTPError as e:
        return (False, e.code, e.read())
    except Exception as e:
        return (False, 0, str(e).encode())

print("=" * 65)
print("  AGENT END-TO-END CONNECTIVITY TEST")
print("=" * 65)

# ── Phase 1: Config Audit ──────────────────────────────────
print("\n[Phase 1] Agent Configuration Audit")

h = yaml.safe_load(open("/root/.hermes/config.yaml"))
cp = h.get("custom_providers", [])
if isinstance(cp, list):
    bad = [p["name"] for p in cp if "8337" not in p.get("base_url", "")]
    p(f"Hermes: {len(cp)} providers, {'' if not bad else str(bad)+' NOT'} all → 8337") if not bad else f(f"Hermes: {bad} NOT 8337")

oc = json.load(open("/root/.config/opencode/opencode.jsonc"))
ocp = oc.get("provider", {})
bad = [n for n, pv in ocp.items() if "8337" not in pv.get("options", {}).get("baseURL", "")]
p(f"OpenCode: {len(ocp)} providers, all → 8337") if not bad else f(f"OpenCode: {bad} NOT 8337")

pi = json.load(open("/root/.pi/agent/models.json"))
pip = pi.get("providers", {})
bad = [n for n, pv in pip.items() if "8337" not in pv.get("baseUrl", "")]
p(f"PI: {len(pip)} providers, all → 8337") if not bad else f(f"PI: {bad} NOT 8337")

omp = yaml.safe_load(open("/root/.omp/agent/models.yml"))
ompp = omp.get("providers", {})
bad = [n for n, pv in ompp.items() if "8337" not in pv.get("baseUrl", "")]
p(f"OMP: {len(ompp)} providers, all → 8337") if not bad else f(f"OMP: {bad} NOT 8337")

# ── Phase 2: Proxy Component Test ──────────────────────────
print("\n[Phase 2] Proxy Component Test")

ok, code, data = http_get("/v1/models")
if ok:
    models = json.loads(data)
    p(f"/v1/models: {len(models.get('data',[]))} models")
else:
    f(f"/v1/models: HTTP {code}")

ok, code, data = http_get("/unknown")
p(f"Unknown path: HTTP {code} (expected 404)") if code == 404 else f(f"Unknown path: HTTP {code}")

data = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}).encode()
req = urllib.request.Request("http://127.0.0.1:8337/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        f("Whitelist: gpt-4 should be blocked")
except urllib.request.HTTPError as e:
    p(f"Whitelist: gpt-4 blocked (HTTP {e.code})") if e.code == 403 else w(f"Whitelist: gpt-4 → HTTP {e.code}")
except Exception as e:
    w(f"Whitelist: {e}")

# ── Phase 3: Agent Routing Test ────────────────────────────
print("\n[Phase 3] Agent Routing (model IDs → upstream via 8337)")

for provider, model in [("openrouter","or/nemotron-3-nano:free"),("sensenova","nova/glm-5.2"),("stepfun","sf/step-3.5-flash"),("groq","gq/gpt-oss-20b")]:
    # nous intentionally omitted: its only two manual model entries were
    # removed 2026-08-02 (one confirmed broken in real production use,
    # the other unverified with the same risk profile) -- there's
    # currently nothing valid to route to for this provider until a real
    # verified entry is added back. See registry.default.yaml's nous:
    # block for the full explanation.
    success, detail = proxy_call(model, timeout=15)
    p(f"{model} → {detail}") if success else w(f"{model} → {detail}")

# ── Phase 4: Service Health ────────────────────────────────
print("\n[Phase 4] Service Health")
for svc in ["open-free-router", "opencode"]:
    r = subprocess.run(["systemctl", "is-active", f"{svc}.service"], capture_output=True, text=True, timeout=5)
    p(f"{svc}: {r.stdout.strip()}") if r.stdout.strip() == "active" else f(f"{svc}: {r.stdout.strip()}")

# ── Phase 5: Unit Tests ────────────────────────────────────
print("\n[Phase 5] Unit Tests")
r = subprocess.run(["python3", "-m", "pytest", "tests/", "-q"], capture_output=True, text=True, timeout=30, cwd="/root/.openclaw/workspace/open-free-router")
lines = r.stdout.strip().split("\n")
last = lines[-1] if lines else "?"
p(f"pytest: {last}") if "passed" in last else f(f"pytest: {last}")

total = PASS + FAIL + WARN
print(f"\n{'='*65}")
print(f"  RESULT: {PASS}✅ / {FAIL}❌ / {WARN}⚠️  / {total} total")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
