"""
A deliberately *legacy-styled* mock back-office banking app.

Design goals (this is the "hostile surface" stand-in from the brief):
  - server-rendered HTML, table-based layout, inline styles
  - NO data-testid / stable ids on the controls that matter
  - generic, reused class names ("fld", "btn", "row")
  - realistic runtime error / exceptional states that a replay must handle,
    triggerable ON DEMAND so we can prove the error taxonomy works:

      member_id == "00000"  -> business outcome: record not found
      member_id == "99999"  -> permission denied (403)
      member_id == "55555"  -> triggers an unexpected interstitial dialog
      ?simulate=timeout      -> transient slow/error load (recoverable w/ retry)
      subaccount type blank  -> validation error

This is intentionally NOT a clean DOM. Locators must lean on visible label
text and table structure, exactly like the real environment.
"""
from __future__ import annotations

import time
from flask import Flask, request, redirect, make_response, session

app = Flask(__name__)
app.secret_key = "mock-bank-not-a-real-secret"  # only for the demo session cookie

# In-memory "core banking" data. Balances are the sensitive figures.
MEMBERS = {
    "12345": {"name": "Alicia Fenwick", "savings": "4,182.55", "checking": "902.10"},
    "23456": {"name": "Marcus Delgado", "savings": "17.00", "checking": "1,240.00"},
    "55555": {"name": "Priya Raman", "savings": "88,120.42", "checking": "40.00"},
    "77777": {"name": "Dev Okafor", "savings": "231.09", "checking": "58.20"},
    "60503": {"name": "Nadia Toft", "savings": "3,006.77", "checking": "12.00"},
}

# Injected transient failure: first N hits to /search "time out", then recover.
_transient_counter = {"n": 0}


def reset_transient_state():
    """Reset injected transient-failure counters.

    The CLI starts/stops the mock server fresh for every command, so these
    counters were implicitly reset between runs. The API server keeps the
    mock app running continuously across many requests, so callers that want
    "fresh process" demo semantics for a single discovery/replay call this
    first.
    """
    _transient_counter["n"] = 0
    _transient_counter["m60503"] = 0

PAGE = """<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body style="font-family:Verdana,Arial;font-size:12px;background:#e8e8e8;margin:0">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:#0b3d62;color:#fff;padding:8px 14px;font-size:15px;font-weight:bold">
CoreServ 7.2 &nbsp;&mdash;&nbsp; Member Services Console</td></tr>
<tr><td style="background:#c9d7e2;padding:4px 14px;font-size:11px">
<a href="/search">Search</a> &nbsp;|&nbsp; <a href="/logout">Sign off</a>
&nbsp;&nbsp; <span style="color:#555">operator: {op}</span></td></tr>
</table>
<table width="100%" cellpadding="10"><tr><td>{body}</td></tr></table>
</body></html>"""


def render(title, body):
    op = session.get("user", "&lt;not signed in&gt;")
    return PAGE.format(title=title, body=body, op=op)


def require_login():
    return "user" in session


@app.route("/")
def index():
    if require_login():
        return redirect("/search")
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Any non-empty creds accepted; we never store the password anywhere.
        if request.form.get("username") and request.form.get("password"):
            session["user"] = request.form["username"]
            return redirect("/search")
        body = "<p style='color:#900'>Enter a username and password.</p>" + _login_form()
        return render("Sign on", body)
    return render("Sign on", _login_form())


def _login_form():
    # No ids/test-ids; fields identified only by their visible labels.
    return """
    <table cellpadding="4"><form method="post" action="/login">
      <tr><td>User ID</td><td><input type="text" name="username" class="fld"></td></tr>
      <tr><td>Password</td><td><input type="password" name="password" class="fld"></td></tr>
      <tr><td></td><td><input type="submit" class="btn" value="Sign On"></td></tr>
    </form></table>"""


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/search", methods=["GET", "POST"])
def search():
    if not require_login():
        return redirect("/login")

    # Injected transient failure (recoverable). Recovers after 1 failure.
    if request.args.get("simulate") == "timeout":
        _transient_counter["n"] += 1
        if _transient_counter["n"] <= 1:
            time.sleep(0.2)
            resp = make_response(render(
                "Temporary error",
                "<p class='err'>The service is temporarily unavailable (E-503). "
                "Please retry.</p>"))
            resp.status_code = 503
            return resp

    if request.method == "POST":
        mid = (request.form.get("member_id") or "").strip()
        if not mid:
            return render("Search", "<p style='color:#900'>Member ID is required.</p>"
                          + _search_form())
        return redirect(f"/member/{mid}")

    return render("Search", _search_form())


def _search_form():
    return """
    <b>Member lookup</b>
    <table cellpadding="4"><form method="post" action="/search">
      <tr><td>Member ID</td><td><input type="text" name="member_id" class="fld"></td></tr>
      <tr><td></td><td><input type="submit" class="btn" value="Search"></td></tr>
    </form></table>"""


@app.route("/member/<mid>")
def member_detail(mid):
    if not require_login():
        return redirect("/login")

    if mid == "60503":
        # One-time transient failure on detail load; recovers on retry.
        n = _transient_counter.get("m60503", 0) + 1
        _transient_counter["m60503"] = n
        if n <= 1:
            resp = make_response(render(
                "Temporary error",
                "<p class='err'>The service is temporarily unavailable (E-503). "
                "Please retry.</p>"))
            resp.status_code = 503
            return resp
        # fall through to normal rendering on subsequent hits

    if mid == "99999":
        resp = make_response(render(
            "Access denied",
            "<p class='err'>You are not authorized to view this member (P-401)."
            "</p>"))
        resp.status_code = 403
        return resp

    if mid == "00000" or mid not in MEMBERS:
        resp = make_response(render(
            "Not found",
            f"<p class='err'>No member found for ID {mid}.</p>"
            "<p><a href='/search'>Back to search</a></p>"))
        resp.status_code = 404
        return resp

    if mid == "55555" and not request.args.get("ack"):
        # Unexpected interstitial: a compliance acknowledgement gate.
        return render(
            "Notice",
            "<div class='dialog'><b>Privacy notice</b>"
            "<p>This member has elevated privacy flags. Acknowledge to continue."
            "</p><a href='/member/55555?ack=1' class='btn'>Acknowledge</a></div>")

    if mid == "77777" and not request.args.get("override"):
        # Unexpected state with NO declared outcome and NO automatic recovery:
        # the balances table is absent and the only way forward is a manual
        # supervisor override the automation was never taught about. This is the
        # escalation trigger -- a human must act on the live session.
        return render(
            "Account maintenance",
            "<p class='err'>This account is under maintenance and cannot be "
            "displayed in the standard view.</p>"
            f"<p><a href='/member/{mid}?override=1' class='btn'>"
            "Override (supervisor)</a></p>")

    m = MEMBERS[mid]
    body = f"""
    <b>Member {mid} &mdash; {m['name']}</b>
    <table cellpadding="4" style="margin-top:8px;background:#fff">
      <tr><td>Account</td><td>Balance</td></tr>
      <tr><td>Savings</td><td class="amt">{m['savings']}</td></tr>
      <tr><td>Checking</td><td class="amt">{m['checking']}</td></tr>
    </table>
    <p style="margin-top:10px">
      <a href="/member/{mid}/subaccount" class="btn">Open sub-account</a></p>
    """
    return render("Member detail", body)


@app.route("/member/<mid>/subaccount", methods=["GET", "POST"])
def subaccount(mid):
    if not require_login():
        return redirect("/login")
    if mid not in MEMBERS:
        resp = make_response(render("Not found", f"<p class='err'>No member {mid}.</p>"))
        resp.status_code = 404
        return resp

    if request.method == "POST":
        acct_type = (request.form.get("account_type") or "").strip()
        if not acct_type:
            # Validation error (business-relevant, not a crash).
            return render("Open sub-account",
                          "<p style='color:#900'>Account type is required.</p>"
                          + _subaccount_form(mid))
        ref = f"SA-{mid}-{acct_type[:3].upper()}"
        body = f"""
        <b>Confirmation</b>
        <table cellpadding="4" style="margin-top:8px;background:#fff">
          <tr><td>Member</td><td>{mid}</td></tr>
          <tr><td>New sub-account</td><td>{acct_type}</td></tr>
          <tr><td>Reference</td><td class="ref">{ref}</td></tr>
        </table>
        <p class="ok">Sub-account created successfully.</p>"""
        return render("Confirmation", body)

    return render("Open sub-account", _subaccount_form(mid))


def _subaccount_form(mid):
    return f"""
    <b>Open sub-account for member {mid}</b>
    <table cellpadding="4"><form method="post" action="/member/{mid}/subaccount">
      <tr><td>Account type</td><td>
        <select name="account_type" class="fld">
          <option value="">-- select --</option>
          <option value="Savings">Savings</option>
          <option value="Money Market">Money Market</option>
        </select></td></tr>
      <tr><td></td><td><input type="submit" class="btn" value="Create"></td></tr>
    </form></table>"""


def create_app():
    return app


if __name__ == "__main__":
    app.run(port=5001, debug=False)
