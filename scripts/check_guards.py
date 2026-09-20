"""Local-browser freshness/execution regressions. No model calls or external websites."""

import base64
import time
from io import BytesIO
from urllib.parse import quote

from PIL import Image

from jev_ultrafast.browser import Browser, Paused, StalePage


def pointer_pixel(browser, point, timeout=8.0, dark=600):
    """Wait until a streamed frame draws the pointer dark at a point, then return it.

    Chrome streams a frame only when the page changes, so a still page sends nothing and
    the move has to come first. Waiting for a frame at the destination, rather than
    reading whichever frame happens to be newest, is also what a viewer means: within a
    moment of the move, the pointer is visible there.
    """
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        frame = browser.frame()
        if frame:
            image = Image.open(BytesIO(base64.b64decode(frame))).convert("RGB")
            seen = sum(image.getpixel(point))
            if seen < dark:
                return image
        time.sleep(0.05)
    raise AssertionError(f"no frame drew the pointer at {point}; last value {seen}")


HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<form role="search" method="get"><textarea aria-label="Site search">example query</textarea>'
            '<button type="submit" style="width:0;padding:0;border:0">Search</button></form>'
            '<p id="out">Waiting</p><script>'
            'const field=document.querySelector("textarea");'
            'field.addEventListener("keydown",event=>{'
            'if(event.key==="Enter"){event.preventDefault();field.form.requestSubmit()}});'
            'field.form.addEventListener("submit",event=>{'
            'event.preventDefault();document.querySelector("#out").textContent="Submitted: "+field.value});'
            '</script>'
        ))
        page = browser.observe(screenshot=False)
        submit = next(a for a in page["actions"] if a["kind"] == "submit")
        assert submit["label"] == "Submit search with Enter"
        assert submit["value"] == "example query"
        assert not any(a["label"] == "Open Site search" for a in page["actions"])
        browser.act(submit, page)
        assert "Submitted: example query" in browser.observe(screenshot=False)["text"]
        passed.append("populated search form submits with Enter when its button is hidden")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<button>Search</button><div style="position:fixed;inset:20px;background:white">'
            'Please complete the following challenge to confirm this search was made by a human.'
            '<p>Select all squares containing a duck:</p><button>Submit</button></div>'
        ))
        page = browser.observe(screenshot=False)
        assert page["captcha"] == {"provider": "unknown", "surface": "challenge_page"}
        assert page["actions"] == []
        passed.append("visible human-verification image challenge blocks normal actions")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<button id="go" onclick="window.clicks=(window.clicks||0)+1">Go</button>'
            '<div class="g-recaptcha" style="display:none">Invisible placeholder</div>'
        ))
        page = browser.observe(screenshot=False)
        assert page.get("captcha") is None
        go = next(a for a in page["actions"] if a["label"] == "Go")
        passed.append("hidden CAPTCHA placeholder does not block normal controls")

        browser.evaluate("document.body.insertAdjacentHTML('beforeend'," + repr(
            '<iframe title="reCAPTCHA" width="304" height="78" srcdoc="Check"></iframe>'
        ) + ")")
        assert not browser.fresh(page, go)
        try:
            browser.act(go, page)
        except StalePage:
            pass
        else:
            raise AssertionError("A button was clicked after a CAPTCHA appeared")
        assert browser.evaluate("window.clicks || 0") == 0
        page = browser.observe(screenshot=False)
        assert page["captcha"] == {"provider": "recaptcha", "surface": "widget"}
        assert page["actions"] == []
        passed.append("visible CAPTCHA blocks a stale action without a model call")

        for provider, source in {
            "hcaptcha": "https://newassets.hcaptcha.com/captcha/v1/fixture",
            "turnstile": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/fixture",
            "arkose": "https://client-api.arkoselabs.com/fc/fixture",
        }.items():
            browser.call("Page.navigate", url="data:text/html," + quote(
                f'<iframe width="304" height="78" src="{source}" srcdoc="Check"></iframe>'
            ))
            page = browser.observe(screenshot=False)
            assert page["captcha"] == {"provider": provider, "surface": "widget"}
        passed.append("visible hCaptcha, Turnstile, and Arkose frames are detected")

        for provider, selector in {
            "geetest": "geetest_captcha",
            "friendlycaptcha": "frc-captcha",
        }.items():
            browser.call("Page.navigate", url="data:text/html," + quote(
                f'<div class="{selector}" style="width:260px;height:50px">Verify</div>'
            ))
            page = browser.observe(screenshot=False)
            assert page["captcha"] == {"provider": provider, "surface": "widget"}
            assert page["actions"] == []
        passed.append("visible GeeTest and Friendly Captcha containers are detected")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<title>Just a moment...</title><form id="challenge-form" '
            'action="/cdn-cgi/challenge-platform/verify"><button>Verify</button></form>'
        ))
        page = browser.observe(screenshot=False)
        assert page["captcha"] == {"provider": "cloudflare", "surface": "challenge_page"}
        passed.append("Cloudflare challenge page is detected")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<title>Human input</title>'
            '<button id="human" onclick="window.humanClicks=(window.humanClicks||0)+1">Click</button>'
            '<input id="humanText" aria-label="Human text">'
        ))
        for _ in range(20):
            if browser.observe(screenshot=False)["title"] == "Human input":
                break
        else:
            raise AssertionError("Human input fixture did not load")
        view = browser.human_view()
        image = Image.open(BytesIO(base64.b64decode(view["screenshot"])))
        assert image.size == (view["width"], view["height"]) == (1120, 780)
        for selector in ("#human", "#humanText"):
            point = browser.evaluate(
                f"(() => {{ const r=document.querySelector('{selector}').getBoundingClientRect(); "
                "return {x:r.x+r.width/2,y:r.y+r.height/2}; })()"
            )
            browser.human_input({"kind": "pointer_down", **point})
            browser.human_input({"kind": "pointer_up", **point})
        browser.human_input({"kind": "key", "key": "A", "code": "KeyA", "modifiers": 8})
        assert browser.evaluate("window.humanClicks") == 1
        assert browser.evaluate("document.querySelector('#humanText').value") == "A"
        passed.append("human screen input clicks and types in the owned tab")

        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")

        browser.call("Page.navigate", url="data:text/html," + quote(
            '<title>Pause</title>'
            '<button id="paused" onclick="window.pauseClicks=(window.pauseClicks||0)+1">Continue</button>'
        ))
        for _ in range(20):
            if browser.observe(screenshot=False)["title"] == "Pause":
                break
        else:
            raise AssertionError("Pause fixture did not load")
        pause_page = browser.observe(screenshot=False)
        pause_action = next(a for a in pause_page["actions"] if a["label"] == "Continue")
        browser.set_paused(True)
        try:
            browser.act(pause_action, pause_page)
        except Paused:
            pass
        else:
            raise AssertionError("A paused browser executed an action")
        assert not browser.evaluate("window.pauseClicks"), "A paused browser touched the page"
        # A pause must not be a dead end: continuing lets the same action through.
        browser.set_paused(False)
        browser.next_action_at = 0
        browser.act(pause_action, pause_page)
        assert browser.evaluate("window.pauseClicks") == 1
        passed.append("a paused browser takes no action and continues on resume")

        # Text a person cannot see must not read as page content, while ordinary
        # overflow:hidden layout and truncation must still be reported. A one-pixel
        # helper is the case that overlap alone lets through, and a visible card the
        # case that a strict containment test wrongly drops.
        browser.call("Page.navigate", url="data:text/html," + quote(
            '<title>Reader</title>'
            '<div style="overflow:hidden"><h2>Card title</h2><p>Card body</p></div>'
            '<div style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;'
            'width:120px">Truncated value here</div>'
            '<span style="position:absolute;width:1px;height:1px;overflow:hidden;'
            'clip:rect(0 0 0 0);white-space:nowrap">Hidden helper label</span>'
            '<div style="height:0;overflow:hidden">Zero height notice</div>'
            '<p style="color:transparent">Transparent notice</p>'
        ))
        for _ in range(20):
            if browser.observe(screenshot=False)["title"] == "Reader":
                break
        else:
            raise AssertionError("Reader fixture did not load")
        reader_text = browser.observe(screenshot=False)["text"]
        assert "Card title" in reader_text and "Card body" in reader_text, reader_text
        assert "Truncated value" in reader_text, reader_text
        for unseen in ("Hidden helper label", "Zero height notice", "Transparent notice"):
            assert unseen not in reader_text, f"{unseen!r} was reported as page text"
        passed.append("text no one can see is not reported, while clipped layout still is")

        # The visible pointer is an ordinary page element, so the only honest way
        # to confirm it is to look at the picture a viewer would see. Nothing here
        # sets a flag: the pointer is unconditional, and this is what proves it.
        pointer = Browser("data:text/html," + quote(
            '<title>Pointer</title><div style="position:fixed;inset:0;background:white"></div>'
            '<button style="position:fixed;left:40px;top:40px;width:200px;height:120px">A</button>'
            '<button style="position:fixed;left:400px;top:600px;width:200px;height:120px">B</button>'
        ))
        try:
            pointer.observe(screenshot=False)
            pointer.start_live()
            pointer.move_pointer((400.0, 300.0))
            image = pointer_pixel(pointer, (400, 300))
            assert min(image.getpixel((900, 600))) > 240, image.getpixel((900, 600))
            pointer.move_pointer((700.0, 500.0))
            image = pointer_pixel(pointer, (700, 500))
            # Moving on must leave the old spot clean again.
            assert min(image.getpixel((400, 300))) > 240, image.getpixel((400, 300))
            passed.append("the streamed pointer is drawn where it moves and the picture follows it")

            # Crossing between elements must not hide the pointer. A capture-phase
            # mouseleave listener on the document saw the mouseleave of every
            # element the pointer left, so it vanished at random on an ordinary page.
            pointer.move_pointer((140.0, 100.0))
            pointer.move_pointer((500.0, 660.0))
            hidden = pointer.evaluate(
                "document.querySelector('jev-cursor').getAttribute('data-hidden')"
            )
            assert hidden is None, "the pointer hid itself after crossing elements"
            passed.append("crossing elements does not hide the pointer")

            # The pointer must leave no global behind. A window property named
            # after this project is the shape of a driver leftover, and the
            # detection probe flags exactly that.
            assert pointer.evaluate("typeof window.__jevCursor") == "undefined"
            passed.append("the pointer leaves no global behind")
        finally:
            pointer.close()
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
