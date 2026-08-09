"""The staff portal: who may sign in, and what they can see once they have.

The access-control tests matter most here. Every route is scoped to the caller
server-side, and the whole point of the portal is that an employee sees their
own downline and nobody else's -- so the tests that would catch a leak are the
ones asserting what a second employee does *not* get back.
"""

import io
import uuid


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
