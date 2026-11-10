from fastapi.testclient import TestClient

from backend.app import create_app


def no_model_calls(*args, **kwargs):
    raise AssertionError("frontend tests must not invoke a model")


def build_ui(root):
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><title>产品经理工作台</title>'
        '<script type="module" src="/assets/app.js"></script></html>', encoding="utf-8")
    (root / "assets" / "app.js").write_text('console.log("workbench");', encoding="utf-8")


def client_for(tmp_path, ui):
    return TestClient(create_app(tmp_path / "data", no_model_calls, ui_root=ui),
                      base_url="http://127.0.0.1:8787")


def test_frontend_home_assets_docs_and_existing_api(tmp_path):
    ui = tmp_path / "ui"
    build_ui(ui)
    with client_for(tmp_path, ui) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 200
        assert home.headers["content-type"].startswith("text/html")
        assert "产品经理工作台" in home.text and "/assets/app.js" in home.text
        assert "location" not in home.headers
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200 and asset.text == 'console.log("workbench");'
        assert "javascript" in asset.headers["content-type"]
        for response in (home, asset):
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/docs").status_code == 200
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post("/api/projects", json={"name": "测试工作台", "idea": "保存个人需求"})
        assert created.status_code == 201
        assert client.get("/api/projects").json()["items"][0]["id"] == created.json()["id"]


def test_frontend_retains_origin_protection_and_confines_files(tmp_path):
    ui = tmp_path / "ui"
    build_ui(ui)
    secret = "private-synthetic-project-data"
    private = tmp_path / "private.txt"
    private.write_text(secret, encoding="utf-8")
    (ui / "assets" / "leak.js").symlink_to(private)
    with client_for(tmp_path, ui) as client:
        created = client.post("/api/projects", json={"name": "私有测试产品", "idea": secret})
        assert created.status_code == 201
        for path in ("/", "/assets/app.js"):
            for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"},
                            {"Sec-Fetch-Site": "cross-site"}):
                assert client.get(path, headers=headers).status_code == 403
        for path in ("/assets/%2e%2e/index.html", "/assets/%2e%2e/%2e%2e/private.txt",
                     "/assets/leak.js", "/data/workbench.sqlite3", "/.env",
                     "/workbench-ui/dist/index.html"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.json()["error"]["code"] == "HTTP_ERROR"
            assert secret not in response.text and str(tmp_path) not in response.text
        (ui / "index.html").unlink()
        (ui / "index.html").symlink_to(private)
        assert client.get("/").status_code == 503
        (ui / "assets" / "leak.js").unlink()
        (ui / "assets" / "app.js").unlink()
        (ui / "assets").rmdir()
        (ui / "assets").symlink_to(tmp_path)
        # 新挂载还未通过配置校验，assets 根目录也不得越过构建目录。
    with client_for(tmp_path, ui) as client:
        response = client.get("/assets/private.txt")
        assert response.status_code == 404
        assert secret not in response.text and str(tmp_path) not in response.text


def test_missing_frontend_is_explicit_and_can_recover_after_build(tmp_path):
    ui = tmp_path / "not-built-yet"
    with client_for(tmp_path, ui) as client:
        for path in ("/", "/assets/app.js"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "UI_NOT_BUILT"
            assert "尚未构建" in response.json()["error"]["message"]
            assert "location" not in response.headers
            assert str(tmp_path) not in response.text
        assert client.get("/api/health").status_code == 200
        assert client.get("/docs").status_code == 200
        build_ui(ui)
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
