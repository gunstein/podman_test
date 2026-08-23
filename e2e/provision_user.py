import json
import os
import urllib.error
import urllib.parse
import urllib.request


KEYCLOAK_URL = "http://127.0.0.1:8080/auth"
REALM = "todo"
USERNAME = "testuser"


def request(url, *, method="GET", token=None, data=None, content_type=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    body = data.encode() if isinstance(data, str) else data
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, headers=headers, method=method)
        ) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Keycloak returned HTTP {error.code}: {detail}") from error


def json_request(url, *, method="GET", token=None, value=None):
    data = None if value is None else json.dumps(value)
    status, body, headers = request(
        url,
        method=method,
        token=token,
        data=data,
        content_type="application/json" if value is not None else None,
    )
    return status, json.loads(body) if body else None, headers


def admin_token(admin_password):
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "admin",
            "password": admin_password,
        }
    )
    _, body, _ = request(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        method="POST",
        data=form,
        content_type="application/x-www-form-urlencoded",
    )
    return json.loads(body)["access_token"]


def provision(token, password):
    query = urllib.parse.urlencode({"username": USERNAME, "exact": "true"})
    users_url = f"{KEYCLOAK_URL}/admin/realms/{REALM}/users"
    _, users, _ = json_request(f"{users_url}?{query}", token=token)

    profile = {
        "username": USERNAME,
        "enabled": True,
        "email": "testuser@example.invalid",
        "emailVerified": True,
        "firstName": "Test",
        "lastName": "User",
        "requiredActions": [],
    }

    if users:
        user_id = users[0]["id"]
        json_request(f"{users_url}/{user_id}", method="PUT", token=token, value=profile)
    else:
        _, _, headers = json_request(
            users_url, method="POST", token=token, value=profile
        )
        user_id = headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    credential = {"type": "password", "value": password, "temporary": False}
    json_request(
        f"{users_url}/{user_id}/reset-password",
        method="PUT",
        token=token,
        value=credential,
    )


def main():
    admin_password = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
    test_password = os.environ["E2E_PASSWORD"]
    try:
        provision(admin_token(admin_password), test_password)
    except RuntimeError as error:
        raise SystemExit(f"Could not provision Keycloak testuser: {error}") from error


if __name__ == "__main__":
    main()
