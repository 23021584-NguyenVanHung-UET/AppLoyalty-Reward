import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from .test_http_auth import AUTH_SETTINGS


@override_settings(**AUTH_SETTINGS)
class FrontendServingTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / ".vite").mkdir()
        (self.root / "assets").mkdir()
        (self.root / ".vite" / "manifest.json").write_text(json.dumps({
            "src/main.ts": {"file": "assets/main-abcdefgh.js", "css": ["assets/main-abcdefgh.css"],
                            "imports": ["shared"]},
            "shared": {"file": "assets/shared-abcdefgh.js", "css": ["assets/shared-abcdefgh.css"]},
        }), encoding="utf-8")
        (self.root / "assets" / "main-abcdefgh.js").write_text("export {};", encoding="utf-8")
        self.override = override_settings(FRONTEND_DIST_DIR=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_manifest_assets_and_iframe_headers(self):
        response = self.client.get("/app/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="shopify-api-key" content="test-client">')
        for asset in ("main-abcdefgh.js", "main-abcdefgh.css", "shared-abcdefgh.css", "shared-abcdefgh.js"):
            self.assertContains(response, f"/app-static/assets/{asset}")
        self.assertContains(response, "https://cdn.shopify.com/shopifycloud/app-bridge.js")
        self.assertContains(response, "https://cdn.shopify.com/shopifycloud/polaris-1.1.js")
        self.assertNotIn("X-Frame-Options", response)
        self.assertEqual(response["Content-Security-Policy"],
                         "frame-ancestors https://admin.shopify.com https://test.myshopify.com")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_diagnostic_keeps_original_behavior(self):
        response = self.client.get("/app/diagnostic/")
        self.assertContains(response, "window.shopify.idToken()")
        self.assertContains(response, "/api/admin/me")
        self.assertNotIn("X-Frame-Options", response)

    def test_spa_deep_links_render_the_app_shell_with_iframe_headers(self):
        for path in ("customers?search=Test&page=2", "customers/21", "rewards", "redemptions",
                     "settings", "webhooks", "health", "unknown-page"):
            with self.subTest(path=path):
                response = self.client.get(f"/app/{path}")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "/app-static/assets/main-abcdefgh.js")
                self.assertNotIn("X-Frame-Options", response)
                self.assertIn("frame-ancestors", response["Content-Security-Policy"])
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_spa_fallback_is_get_only_and_does_not_capture_other_paths(self):
        self.assertEqual(self.client.post("/app/customers", {}).status_code, 405)
        self.assertEqual(self.client.get("/not-an-app-route").status_code, 404)
        self.assertEqual(self.client.get("/app-static/assets/missing-abcdefgh.js").status_code, 404)

    def test_only_hashed_assets_are_served_and_cached(self):
        response = self.client.get("/app-static/assets/main-abcdefgh.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "public, max-age=31536000, immutable")
        response.close()
        for path in ("/app-static/assets/../.vite/manifest.json", "/app-static/assets/plain.js",
                     "/app-static/assets/missing-abcdefgh.js"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_missing_or_unsafe_manifest_is_helpful_not_a_traceback(self):
        manifest = self.root / ".vite" / "manifest.json"
        manifest.unlink()
        self.assertContains(self.client.get("/app/"), "has not been built")
        manifest.write_text(json.dumps({"src/main.ts": {"file": "../secret.js"}}), encoding="utf-8")
        self.assertContains(self.client.get("/app/"), "has not been built")
