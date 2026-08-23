"""The login page.

Deliberately its own small module rather than a corner of dashboard.py: this is
what an unauthenticated visitor sees, so it must render without touching the
store, the identity registry or anything else that could fail.

Same palette and dark-mode handling as the dashboard, so arriving at the site
does not feel like two different products.
"""

from __future__ import annotations


def render(*, error: str = "", next_path: str = "/") -> str:
    safe_error = (
        error.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    safe_next = (
        next_path.replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
    message = (
        f'<p class="error" role="alert">{safe_error}</p>' if safe_error else ""
    )
    return _TEMPLATE.replace("<!--ERROR-->", message).replace("__NEXT__", safe_next)


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cork Tri Club — sign in</title>
<style>
  :root {
    color-scheme: light;
    --plane:#f9f9f7; --surface:#fcfcfb;
    --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
    --accent:#2a78d6; --bad:#d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --plane:#0d0d0d; --surface:#1a1a19;
      --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
      --axis:#383835; --border:rgba(255,255,255,0.10);
      --accent:#3987e5; --bad:#e66767;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --axis:#383835; --border:rgba(255,255,255,0.10);
    --accent:#3987e5; --bad:#e66767;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; min-height:100dvh; display:grid; place-items:center; padding:24px;
    background:var(--plane); color:var(--ink);
    font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  main {
    width:100%; max-width:380px; background:var(--surface);
    border:1px solid var(--border); border-radius:14px; padding:28px 26px 24px;
    box-shadow:0 10px 34px rgba(0,0,0,.07);
  }
  .mark {
    width:42px; height:42px; border-radius:11px; background:var(--accent);
    display:grid; place-items:center; margin-bottom:16px;
  }
  .mark svg { display:block; }
  h1 { margin:0 0 4px; font-size:19px; letter-spacing:-0.02em; }
  p.sub { margin:0 0 20px; color:var(--ink-2); font-size:13.5px; }
  label { display:block; font-size:12px; font-weight:600; color:var(--ink-2);
          text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px; }
  input[type=password] {
    width:100%; font:inherit; color:var(--ink); background:var(--plane);
    border:1px solid var(--axis); border-radius:9px; padding:10px 12px;
  }
  input[type=password]:focus {
    outline:none; border-color:var(--accent);
    box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent);
  }
  button {
    width:100%; margin-top:14px; font:inherit; font-weight:600; cursor:pointer;
    color:#fff; background:var(--accent); border:1px solid var(--accent);
    border-radius:9px; padding:10px 14px;
  }
  button:hover { filter:brightness(1.06); }
  .error {
    margin:0 0 16px; padding:9px 11px; font-size:13.5px;
    color:var(--bad); background:color-mix(in srgb, var(--bad) 10%, transparent);
    border-left:3px solid var(--bad); border-radius:0 7px 7px 0;
  }
  footer { margin-top:18px; color:var(--muted); font-size:12px; text-align:center; }
</style>
</head>
<body>
<main>
  <div class="mark" aria-hidden="true">
    <!-- A bike wheel and a wave: the two things the club times. -->
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff"
         stroke-width="1.8" stroke-linecap="round">
      <circle cx="12" cy="9" r="5.2"/>
      <path d="M12 9 L15 5.6"/>
      <path d="M3 18.4c1.6 0 1.6 1.4 3.2 1.4s1.6-1.4 3.2-1.4 1.6 1.4 3.2 1.4
               1.6-1.4 3.2-1.4 1.6 1.4 3.2 1.4"/>
    </svg>
  </div>

  <h1>Cork Tri Club</h1>
  <p class="sub">Time trial and aquathon results. Enter the club password to continue.</p>

  <!--ERROR-->

  <form method="post" action="/login">
    <input type="hidden" name="next" value="__NEXT__">
    <label for="password">Club password</label>
    <input type="password" id="password" name="password" autocomplete="current-password"
           autofocus required>
    <button type="submit">Sign in</button>
  </form>

  <footer>Results are public on RaceClocker; this page adds the club's own history.</footer>
</main>
</body>
</html>
"""
