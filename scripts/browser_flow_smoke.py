"""Isolated real-browser verification of the live workflow; no cloud/model calls."""

import os
import tempfile
import threading
import time
from pathlib import Path


def main():
    from playwright.sync_api import sync_playwright
    import uvicorn

    with tempfile.TemporaryDirectory() as folder:
        os.environ.update(VERCEL="0", DATABASE_URL="", GROQ_API_KEY="", OPENAI_API_KEY="",
                          PROPOSAL_ENGINE="template", AUTOMATION_DB_PATH=str(Path(folder) / "browser.db"))
        from app.main import app, engine

        original = engine.agents["requirements"].run
        def slow_requirements(inp):
            time.sleep(1.5)  # observable in-flight state without paying for a model
            return original(inp)
        engine.agents["requirements"].run = slow_requirements
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:8766/workflow")
                page.locator('.node').first.wait_for()
                assert page.locator('.node').count() == 10
                page.click('#new-run')
                page.fill('[name="project_name"]', 'Live browser portal')
                page.fill('[name="brief"]', 'Build a portal where customers track support requests and receive email notifications.')
                page.select_option('[name="execution_mode"]', 'automatic')
                page.click('#create-flow [type="submit"]')
                page.wait_for_function("document.querySelector('.node.running') || document.querySelector('.node.completed')", timeout=20000)
                page.wait_for_function("document.querySelector('#event-log').textContent.includes('started')")
                page.click('.node[data-id="client_requirements"]')
                page.click('[data-tab="input"]')
                page.wait_for_function("document.querySelector('#inspect-body').textContent.includes('support requests')")
                # Refresh during execution: the first wave persists and remains
                # recoverable; continue explicitly rather than duplicating it.
                page.reload()
                page.wait_for_function("['Ready','Ready for review'].includes(document.querySelector('#run-status').textContent)", timeout=20000)
                if page.locator('#run-status').inner_text() == 'Ready':
                    page.click('#execute-run')
                page.wait_for_function("document.querySelector('#run-status').textContent === 'Ready for review'", timeout=30000)
                assert page.locator('.node.completed').count() == 8
                assert page.locator('.node.skipped').count() == 2
                page.click('.node[data-id="proposal"]')
                page.click('[data-tab="output"]')
                page.wait_for_function("document.querySelector('#inspect-body').textContent.includes('executive_summary')")
                assert page.locator('#inspect-body a').get_attribute('href').endswith('/proposal/download')
                with page.expect_download() as download:
                    page.locator('#inspect-body a').click()
                assert download.value.suggested_filename.endswith('.docx')
                previous_zoom = page.locator('#zoom-value').inner_text()
                page.click('#zoom-in')
                assert previous_zoom != page.locator('#zoom-value').inner_text()
                page.click('#fit')
                page.click('[data-tab="details"]')
                out = Path('out')
                out.mkdir(exist_ok=True)
                page.screenshot(path=str(out / 'workflow-studio.png'), full_page=True)
                page.reload()
                page.wait_for_function("document.querySelector('#run-status').textContent === 'pack ready'")
                page.set_viewport_size({"width": 390, "height": 844})
                page.click('#fit')
                page.screenshot(path=str(out / 'workflow-studio-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
                print('Browser flow passed: live events, reload/resume, node data, Word download, zoom, responsive layout.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
