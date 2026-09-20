"""Offline contracts for the automation-visibility probe."""

from jev_ultrafast import detection


def clean():
    return {
        "webdriver": False,
        "runtimeLeak": False,
        "driverGlobals": [],
        "headlessUserAgent": False,
        "plugins": 5,
        "chromeObject": "object",
        "nativeToString": True,
        "userAgent": "Mozilla/5.0 Chrome/140",
        "viewport": "1120x780",
        "screen": "1512x982",
    }


def levels(results):
    return {result["name"]: result["level"] for result in results}


def test_a_clean_browser_passes_every_check():
    results = detection.summarize(clean())
    assert all(result["level"] == "pass" for result in results)
    assert detection.worst(results) == "pass"


def test_the_runtime_leak_is_reported_as_a_failure():
    results = detection.summarize(clean() | {"runtimeLeak": True})
    assert levels(results)["CDP Runtime.enable leak"] == "fail"
    assert detection.worst(results) == "fail"


def test_the_webdriver_flag_is_reported_as_a_failure():
    results = detection.summarize(clean() | {"webdriver": True})
    assert levels(results)["navigator.webdriver"] == "fail"
    assert detection.worst(results) == "fail"


def test_a_headless_user_agent_is_reported_as_a_failure():
    results = detection.summarize(clean() | {"headlessUserAgent": True})
    assert levels(results)["Headless user agent"] == "fail"


def test_injected_page_objects_are_named_in_the_result():
    results = detection.summarize(clean() | {"driverGlobals": ["__jevFast", "__playwright"]})
    leak = next(result for result in results if result["name"] == "Driver globals")
    assert leak["level"] == "fail"
    assert "__jevFast" in leak["detail"]
    assert "__playwright" in leak["detail"]


def test_a_viewport_larger_than_the_screen_is_reported_as_a_failure():
    results = detection.summarize(clean() | {"viewport": "1120x780", "screen": "800x600"})
    assert levels(results)["Viewport larger than the screen"] == "fail"
    assert detection.worst(results) == "fail"


def test_a_viewport_that_fits_the_screen_passes():
    results = detection.summarize(clean() | {"viewport": "1280x720", "screen": "1512x982"})
    assert levels(results)["Viewport larger than the screen"] == "pass"


def test_unreadable_size_readings_are_not_treated_as_a_leak():
    results = detection.summarize(clean() | {"viewport": "unknown", "screen": None})
    assert levels(results)["Viewport larger than the screen"] == "pass"


def test_suspicious_signals_warn_without_failing():
    findings = clean() | {"plugins": 0, "chromeObject": "undefined", "nativeToString": False}
    results = detection.summarize(findings)
    assert levels(results)["Plugin count"] == "warn"
    assert levels(results)["window.chrome"] == "warn"
    assert levels(results)["Native function integrity"] == "warn"
    assert detection.worst(results) == "warn"


def test_a_failure_outranks_a_warning():
    results = detection.summarize(clean() | {"plugins": 0, "runtimeLeak": True})
    assert detection.worst(results) == "fail"


def test_an_absent_finding_is_not_read_as_detection():
    # An older probe may not report a key at all; that must not look like a leak.
    results = detection.summarize({})
    assert detection.worst(results) == "pass"


def test_the_inventory_records_values_worth_comparing_between_runs():
    values = detection.inventory(clean())
    assert set(values) == {
        "userAgent", "platform", "languages", "hardwareConcurrency", "deviceMemory",
        "viewport", "screen", "devicePixelRatio", "webglVendor",
    }
    assert values["userAgent"] == clean()["userAgent"]
    # A key the probe did not report stays absent rather than being invented.
    assert values["platform"] is None
