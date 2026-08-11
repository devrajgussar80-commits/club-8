"""The staff portal: who may sign in, and what they can see once they have.

The access-control tests matter most here. Every route is scoped to the caller
server-side, and the whole point of the portal is that an employee sees their
own downline and nobody else's -- so the tests that would catch a leak are the
ones asserting what a second employee does *not* get back.
"""

import io
import uuid

# The deposit flow is several calls long and test_admin.py already models it.
from test_admin import submit_deposit


def make_employee(client, admin_headers, *, username="Recruiter", win_rate=80):
    phone = f"98{uuid.uuid4().int % 100000000:08d}"
    password = "employee-pass"
    response = client.post(
        "/api/admin/team/create",
        json={"phone": phone, "username": username, "password": password,
              "win_rate": win_rate},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return {**response.json(), "phone": phone, "password": password}


def sign_in(client, employee):
    response = client.post(
        "/api/employee/login",
        json={"phone": employee["phone"], "password": employee["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


# ----------------- ACCESS CONTROL -----------------
def test_portal_rejects_a_missing_token(client):
    assert client.get("/api/employee/me").status_code == 401


def test_portal_rejects_a_garbage_token(client):
    response = client.get("/api/employee/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_a_player_cannot_sign_in_to_the_portal(client, register):
    """And is told where to go, rather than that their password is wrong."""
    _, player = register()
    response = client.post(
        "/api/employee/login",
        json={"phone": player["phone"], "password": player["password"]},
    )
    assert response.status_code == 403
    assert "player account" in response.json()["detail"].lower()


def test_a_players_token_is_refused_by_the_portal(client, register):
    headers, _ = register()
    response = client.get("/api/employee/me", headers=headers)
    assert response.status_code == 403


def test_wrong_password_is_refused(client, admin_headers):
    employee = make_employee(client, admin_headers)
    response = client.post(
        "/api/employee/login",
        json={"phone": employee["phone"], "password": "not-it"},
    )
    assert response.status_code == 400


def test_login_accepts_the_number_with_or_without_a_country_code(client, admin_headers):
    employee = make_employee(client, admin_headers)
    for shape in (employee["phone"], f"+91{employee['phone']}", f"91{employee['phone']}"):
        response = client.post(
            "/api/employee/login",
            json={"phone": shape, "password": employee["password"]},
        )
        assert response.status_code == 200, f"{shape} was refused"


def test_revoking_portal_access_takes_effect_on_the_next_request(client, admin_headers):
    """The token stays valid for a week; the flag is what actually gates."""
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)
    assert client.get("/api/employee/me", headers=headers).status_code == 200

    client.put(
        f"/api/admin/team/{employee['id']}",
        json={"win_rate": 80, "is_employee": False},
        headers=admin_headers,
    )
    assert client.get("/api/employee/me", headers=headers).status_code == 403


def test_a_win_rate_change_alone_leaves_portal_access_alone(client, admin_headers):
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)
    response = client.put(
        f"/api/admin/team/{employee['id']}", json={"win_rate": 0}, headers=admin_headers
    )
    assert response.status_code == 200
    assert client.get("/api/employee/me", headers=headers).status_code == 200


# ----------------- WHAT THE PORTAL SHOWS -----------------
def test_a_new_employee_starts_empty(client, admin_headers):
    headers = sign_in(client, make_employee(client, admin_headers))
    stats = client.get("/api/employee/me", headers=headers).json()["stats"]
    assert stats["invited"] == 0
    assert stats["earned"] == 0
    assert client.get("/api/employee/referrals", headers=headers).json()["referrals"] == []
    assert client.get("/api/employee/chain", headers=headers).json()["chain"] == []


def test_a_signup_on_the_code_shows_as_invited_but_unpaid(client, admin_headers):
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)

    phone = f"97{uuid.uuid4().int % 100000000:08d}"
    client.post("/api/auth/register", json={
        "phone": phone, "username": "Invited", "password": "secret123",
        "referral_code": employee["referral_code"],
    })

    stats = client.get("/api/employee/me", headers=headers).json()["stats"]
    assert stats["invited"] == 1
    assert stats["deposited"] == 0
    assert stats["not_deposited"] == 1
    assert stats["earned"] == 0

    referrals = client.get("/api/employee/referrals", headers=headers).json()["referrals"]
    assert len(referrals) == 1
    assert referrals[0]["earned"] is False
    assert referrals[0]["deposited"] is False
    assert "never deposited" in referrals[0]["why"]


def test_referred_phone_numbers_are_masked(client, admin_headers):
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)
    phone = f"97{uuid.uuid4().int % 100000000:08d}"
    client.post("/api/auth/register", json={
        "phone": phone, "username": "Invited", "password": "secret123",
        "referral_code": employee["referral_code"],
    })

    shown = client.get("/api/employee/referrals", headers=headers).json()["referrals"][0]["phone"]
    assert phone not in shown
    assert "•" in shown


def test_one_employee_cannot_see_anothers_downline(client, admin_headers):
    """The leak this whole module exists to catch."""
    mine = make_employee(client, admin_headers, username="Mine")
    theirs = make_employee(client, admin_headers, username="Theirs")

    client.post("/api/auth/register", json={
        "phone": f"96{uuid.uuid4().int % 100000000:08d}",
        "username": "TheirRecruit", "password": "secret123",
        "referral_code": theirs["referral_code"],
    })

    headers = sign_in(client, mine)
    assert client.get("/api/employee/referrals", headers=headers).json()["referrals"] == []
    assert client.get("/api/employee/me", headers=headers).json()["stats"]["invited"] == 0


def test_colleagues_lists_staff_without_their_contact_details(client, admin_headers):
    mine = make_employee(client, admin_headers, username="Mine")
    make_employee(client, admin_headers, username="Theirs")
    headers = sign_in(client, mine)

    body = client.get("/api/employee/colleagues", headers=headers).json()
    names = {c["name"] for c in body["colleagues"]}
    assert {"Mine", "Theirs"} <= names
    assert sum(1 for c in body["colleagues"] if c["is_me"]) == 1
    for colleague in body["colleagues"]:
        assert "phone" not in colleague
        assert "balance" not in colleague


def test_the_chain_nests_a_referrals_own_referrals(client, admin_headers):
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)

    first = client.post("/api/auth/register", json={
        "phone": f"96{uuid.uuid4().int % 100000000:08d}",
        "username": "First", "password": "secret123",
        "referral_code": employee["referral_code"],
    }).json()
    client.post("/api/auth/register", json={
        "phone": f"95{uuid.uuid4().int % 100000000:08d}",
        "username": "Second", "password": "secret123",
        "referral_code": first["user"]["referral_code"],
    })

    body = client.get("/api/employee/chain", headers=headers).json()
    assert body["total"] == 2
    assert len(body["chain"]) == 1
    assert body["chain"][0]["name"] == "First"
    assert [n["name"] for n in body["chain"][0]["invited"]] == ["Second"]
    # Someone else's wallet is not this employee's business.
    assert "balance" not in body["chain"][0]


# ----------------- PHOTOS -----------------
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_admin_can_attach_a_photo_and_the_portal_serves_it(client, admin_headers):
    employee = make_employee(client, admin_headers)
    response = client.post(
        f"/api/admin/team/{employee['id']}/photo",
        files={"file": ("face.png", io.BytesIO(PNG), "image/png")},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    headers = sign_in(client, employee)
    assert client.get("/api/employee/me", headers=headers).json()["employee"]["has_photo"]

    served = client.get(f"/api/employee/photo/{employee['id']}", headers=headers)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG


def test_a_photo_is_not_public(client, admin_headers):
    employee = make_employee(client, admin_headers)
    client.post(
        f"/api/admin/team/{employee['id']}/photo",
        files={"file": ("face.png", io.BytesIO(PNG), "image/png")},
        headers=admin_headers,
    )
    assert client.get(f"/api/employee/photo/{employee['id']}").status_code == 401


def test_a_pdf_is_not_a_photo(client, admin_headers):
    employee = make_employee(client, admin_headers)
    response = client.post(
        f"/api/admin/team/{employee['id']}/photo",
        files={"file": ("cv.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_the_team_list_reports_the_photo_and_the_portal_flag(client, admin_headers):
    employee = make_employee(client, admin_headers)
    client.post(
        f"/api/admin/team/{employee['id']}/photo",
        files={"file": ("face.png", io.BytesIO(PNG), "image/png")},
        headers=admin_headers,
    )
    team = client.get("/api/admin/team", headers=admin_headers).json()["team"]
    row = next(m for m in team if m["id"] == employee["id"])
    assert row["has_photo"] is True
    assert row["is_employee"] is True
    assert row["photo_version"]


def test_deleting_a_photo_clears_it(client, admin_headers):
    employee = make_employee(client, admin_headers)
    client.post(
        f"/api/admin/team/{employee['id']}/photo",
        files={"file": ("face.png", io.BytesIO(PNG), "image/png")},
        headers=admin_headers,
    )
    assert client.delete(
        f"/api/admin/team/{employee['id']}/photo", headers=admin_headers
    ).status_code == 200

    headers = sign_in(client, employee)
    assert client.get(f"/api/employee/photo/{employee['id']}", headers=headers).status_code == 404


# ----------------- GROUPS -----------------
def make_group(client, admin_headers, name="Delhi", note=""):
    response = client.post(
        "/api/admin/groups", json={"name": name, "note": note}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_groups_need_admin(client):
    assert client.get("/api/admin/groups").status_code == 401


def test_a_group_name_cannot_be_reused(client, admin_headers):
    make_group(client, admin_headers, "Delhi")
    # Case-insensitively: "delhi" and "Delhi" are the same group to a human.
    response = client.post(
        "/api/admin/groups", json={"name": "delhi"}, headers=admin_headers
    )
    assert response.status_code == 400


def test_an_employee_can_be_created_straight_into_a_group(client, admin_headers):
    group = make_group(client, admin_headers, "Night shift")
    employee = make_employee(client, admin_headers)
    client.put(
        f"/api/admin/team/{employee['id']}",
        json={"win_rate": 80, "group_id": group["id"]},
        headers=admin_headers,
    )
    row = next(m for m in client.get("/api/admin/team", headers=admin_headers).json()["team"]
               if m["id"] == employee["id"])
    assert row["group_name"] == "Night shift"

    # And the portal tells them which group they are in.
    headers = sign_in(client, employee)
    shown = client.get("/api/employee/me", headers=headers).json()["employee"]["group"]
    assert shown["name"] == "Night shift"
    assert shown["members"] == 1


def test_saving_a_win_rate_does_not_empty_the_group(client, admin_headers):
    """group_id absent means "leave it"; only "" clears it."""
    group = make_group(client, admin_headers, "Mumbai")
    employee = make_employee(client, admin_headers)
    client.put(f"/api/admin/team/{employee['id']}",
               json={"win_rate": 80, "group_id": group["id"]}, headers=admin_headers)

    client.put(f"/api/admin/team/{employee['id']}",
               json={"win_rate": 55}, headers=admin_headers)
    row = next(m for m in client.get("/api/admin/team", headers=admin_headers).json()["team"]
               if m["id"] == employee["id"])
    assert row["group_id"] == group["id"], "a win-rate save emptied the group"

    client.put(f"/api/admin/team/{employee['id']}",
               json={"win_rate": 55, "group_id": ""}, headers=admin_headers)
    row = next(m for m in client.get("/api/admin/team", headers=admin_headers).json()["team"]
               if m["id"] == employee["id"])
    assert row["group_id"] is None


def test_deleting_a_group_keeps_its_people(client, admin_headers):
    group = make_group(client, admin_headers, "Temporary")
    employee = make_employee(client, admin_headers)
    client.put(f"/api/admin/team/{employee['id']}",
               json={"win_rate": 80, "group_id": group["id"]}, headers=admin_headers)

    response = client.delete(f"/api/admin/groups/{group['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["ungrouped"] == 1

    # Still an employee, still able to sign in -- just no group.
    headers = sign_in(client, employee)
    assert client.get("/api/employee/me", headers=headers).json()["employee"]["group"] is None


def test_an_unknown_group_is_refused(client, admin_headers):
    employee = make_employee(client, admin_headers)
    response = client.put(
        f"/api/admin/team/{employee['id']}",
        json={"win_rate": 80, "group_id": "GRPNOPE"},
        headers=admin_headers,
    )
    assert response.status_code == 404


# ----------------- PERFORMANCE -----------------
def test_performance_ranks_by_deposits_not_headcount(client, admin_headers):
    """The whole point: volume of signups is not performance."""
    quality = make_employee(client, admin_headers, username="Quality")
    volume = make_employee(client, admin_headers, username="Volume")

    # Volume signs up three who never pay in.
    for _ in range(3):
        client.post("/api/auth/register", json={
            "phone": f"96{uuid.uuid4().int % 100000000:08d}",
            "username": "Tyre kicker", "password": "secret123",
            "referral_code": volume["referral_code"]})

    # Quality signs up one who deposits, approved by an admin.
    joined = client.post("/api/auth/register", json={
        "phone": f"95{uuid.uuid4().int % 100000000:08d}",
        "username": "Real player", "password": "secret123",
        "referral_code": quality["referral_code"]}).json()
    headers = {"Authorization": f"Bearer {joined['token']}"}
    deposit_id = submit_deposit(client, headers, 5000)
    client.post(f"/api/admin/deposits/{deposit_id}/approve", headers=admin_headers)

    body = client.get("/api/admin/team/performance", headers=admin_headers).json()
    ranked = [e["name"] for e in body["employees"]]
    assert ranked.index("Quality") < ranked.index("Volume"), ranked

    q = next(e for e in body["employees"] if e["name"] == "Quality")
    v = next(e for e in body["employees"] if e["name"] == "Volume")
    assert q["deposits_brought"] == 5000
    assert v["deposits_brought"] == 0
    assert v["invited"] == 3 and v["deposited"] == 0 and v["not_deposited"] == 3
    assert v["conversion"] == 0.0


def test_group_totals_add_up_to_the_employee_rows(client, admin_headers):
    group = make_group(client, admin_headers, "Delhi")
    for name in ("One", "Two"):
        employee = make_employee(client, admin_headers, username=name)
        client.put(f"/api/admin/team/{employee['id']}",
                   json={"win_rate": 80, "group_id": group["id"]}, headers=admin_headers)
        client.post("/api/auth/register", json={
            "phone": f"94{uuid.uuid4().int % 100000000:08d}",
            "username": "Invited", "password": "secret123",
            "referral_code": employee["referral_code"]})

    body = client.get("/api/admin/team/performance", headers=admin_headers).json()
    delhi = next(g for g in body["groups"] if g["name"] == "Delhi")
    assert delhi["members"] == 2
    assert delhi["invited"] == 2
    # Rolled up from the same rows, so the two views cannot disagree.
    assert delhi["invited"] == sum(
        e["invited"] for e in body["employees"] if e["group_name"] == "Delhi")


def test_staff_with_no_group_are_pooled_not_dropped(client, admin_headers):
    make_employee(client, admin_headers, username="Loner")
    body = client.get("/api/admin/team/performance", headers=admin_headers).json()
    assert any(g["name"] == "Ungrouped" for g in body["groups"])
    assert sum(g["members"] for g in body["groups"]) == body["totals"]["staff"]


# ----------------- SELF-SIGNUP AND APPROVAL -----------------
def apply_for_access(client, name="Applicant", note=""):
    phone = f"93{uuid.uuid4().int % 100000000:08d}"
    response = client.post("/api/employee/register", json={
        "username": name, "phone": phone, "password": "applied123", "note": note})
    assert response.status_code == 200, response.text
    return {**response.json(), "phone": phone, "password": "applied123"}


def test_applying_grants_no_access_at_all(client, admin_headers):
    """The whole risk of self-signup: it must not create a working account."""
    applicant = apply_for_access(client)

    response = client.post("/api/employee/login", json={
        "phone": applicant["phone"], "password": applicant["password"]})
    assert response.status_code == 403
    assert "approval" in response.json()["detail"].lower()

    # And it is not counted as staff anywhere until approved.
    assert not any(c["name"] == "Applicant" for c in
                   client.get("/api/admin/team/performance",
                              headers=admin_headers).json()["employees"])


def test_an_application_shows_up_for_the_admin(client, admin_headers):
    applicant = apply_for_access(client, name="Hopeful", note="Night shift please")
    body = client.get("/api/admin/team/requests", headers=admin_headers).json()
    assert body["pending"] == 1
    request = body["requests"][0]
    assert request["username"] == "Hopeful"
    assert request["note"] == "Night shift please"
    assert request["status"] == "pending"
    assert request["id"] == applicant["id"]


def test_approval_turns_it_into_a_working_account(client, admin_headers):
    group = make_group(client, admin_headers, "Evenings")
    applicant = apply_for_access(client)

    response = client.post(
        f"/api/admin/team/requests/{applicant['id']}/approve",
        json={"win_rate": 65, "group_id": group["id"]}, headers=admin_headers)
    assert response.status_code == 200, response.text

    headers = sign_in(client, applicant)
    me = client.get("/api/employee/me", headers=headers).json()
    assert me["employee"]["group"]["name"] == "Evenings"

    row = next(m for m in client.get("/api/admin/team", headers=admin_headers).json()["team"]
               if m["id"] == applicant["id"])
    assert row["is_employee"] is True
    assert row["win_rate"] == 65


def test_rejection_tells_the_applicant_why(client, admin_headers):
    applicant = apply_for_access(client)
    client.post(f"/api/admin/team/requests/{applicant['id']}/reject",
                json={"note": "Not hiring this month."}, headers=admin_headers)

    response = client.post("/api/employee/login", json={
        "phone": applicant["phone"], "password": applicant["password"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "Not hiring this month."


def test_a_decision_cannot_be_made_twice(client, admin_headers):
    applicant = apply_for_access(client)
    client.post(f"/api/admin/team/requests/{applicant['id']}/approve",
                json={}, headers=admin_headers)
    again = client.post(f"/api/admin/team/requests/{applicant['id']}/reject",
                        json={"note": "changed my mind"}, headers=admin_headers)
    assert again.status_code == 400


def test_the_same_number_cannot_apply_twice(client, admin_headers):
    applicant = apply_for_access(client)
    response = client.post("/api/employee/register", json={
        "username": "Again", "phone": applicant["phone"], "password": "applied123"})
    assert response.status_code == 400
    assert "already waiting" in response.json()["detail"].lower()


def test_a_player_number_cannot_apply(client, register):
    _, player = register()
    response = client.post("/api/employee/register", json={
        "username": "Sneaky", "phone": player["phone"], "password": "applied123"})
    assert response.status_code == 400


def test_the_signup_queue_needs_admin(client):
    assert client.get("/api/admin/team/requests").status_code == 401


# ----------------- ACCOUNT CONTROLS -----------------
def test_editing_name_and_number_and_signing_in_with_the_new_one(client, admin_headers):
    employee = make_employee(client, admin_headers)
    new_phone = f"92{uuid.uuid4().int % 100000000:08d}"
    response = client.put(f"/api/admin/team/{employee['id']}/details",
                          json={"username": "Renamed", "phone": new_phone},
                          headers=admin_headers)
    assert response.status_code == 200

    assert client.post("/api/employee/login", json={
        "phone": new_phone, "password": employee["password"]}).status_code == 200
    # The old number stops working, which is the point of changing it.
    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": employee["password"]}).status_code == 400


def test_a_number_already_in_use_is_refused(client, admin_headers):
    first = make_employee(client, admin_headers)
    second = make_employee(client, admin_headers)
    response = client.put(f"/api/admin/team/{second['id']}/details",
                          json={"phone": first["phone"]}, headers=admin_headers)
    assert response.status_code == 400


def test_setting_a_password_replaces_the_old_one(client, admin_headers):
    employee = make_employee(client, admin_headers)
    assert client.post(f"/api/admin/team/{employee['id']}/password",
                       json={"password": "brand-new-pass"},
                       headers=admin_headers).status_code == 200

    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": "brand-new-pass"}).status_code == 200
    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": employee["password"]}).status_code == 400


def test_disabling_an_account_stops_it_signing_in(client, admin_headers):
    employee = make_employee(client, admin_headers)
    headers = sign_in(client, employee)

    client.post(f"/api/admin/team/{employee['id']}/status",
                json={"status": "disabled"}, headers=admin_headers)
    # Both a fresh sign-in and an existing session.
    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": employee["password"]}).status_code == 403
    assert client.get("/api/employee/me", headers=headers).status_code == 403

    client.post(f"/api/admin/team/{employee['id']}/status",
                json={"status": "active"}, headers=admin_headers)
    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": employee["password"]}).status_code == 200


def test_deletion_impact_counts_what_would_be_lost(client, admin_headers):
    employee = make_employee(client, admin_headers)
    client.post("/api/auth/register", json={
        "phone": f"91{uuid.uuid4().int % 100000000:08d}",
        "username": "Theirs", "password": "secret123",
        "referral_code": employee["referral_code"]})

    impact = client.get(f"/api/admin/team/{employee['id']}/deletion-impact",
                        headers=admin_headers).json()
    assert impact["referrals"] == 1
    assert impact["is_admin"] is False


def test_deleting_an_account_removes_it(client, admin_headers):
    employee = make_employee(client, admin_headers)
    assert client.delete(f"/api/admin/team/{employee['id']}",
                         headers=admin_headers).status_code == 200
    assert client.post("/api/employee/login", json={
        "phone": employee["phone"], "password": employee["password"]}).status_code == 400


def test_an_admin_account_cannot_be_deleted_from_the_team_tab(client, admin_headers, db):
    employee = make_employee(client, admin_headers)
    conn = db()
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (employee["id"],))
    conn.commit()
    conn.close()

    response = client.delete(f"/api/admin/team/{employee['id']}", headers=admin_headers)
    assert response.status_code == 400
    assert "admin" in response.json()["detail"].lower()
