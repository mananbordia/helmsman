(() => {
  // Reports what a page can learn about this browser beyond ordinary use.
  //
  // This is a measurement probe, not a defence. It answers "what is visible
  // right now", so a change can be judged against a baseline instead of a
  // feeling. Every value is best-effort: a failing check reports its error
  // rather than throwing, because a page that breaks the probe has already
  // told us something.
  const safe = (fn) => {
    try {
      return fn();
    } catch (error) {
      return 'error: ' + (error && error.message ? error.message : 'unknown');
    }
  };

  const findings = {
    webdriver: safe(() => navigator.webdriver),
    userAgent: safe(() => navigator.userAgent),
    platform: safe(() => navigator.platform),
    languages: safe(() => (navigator.languages || []).join(',')),
    plugins: safe(() => navigator.plugins.length),
    mimeTypes: safe(() => navigator.mimeTypes.length),
    hardwareConcurrency: safe(() => navigator.hardwareConcurrency),
    deviceMemory: safe(() => navigator.deviceMemory),
    chromeObject: safe(() => typeof window.chrome),
    notificationPermission: safe(() => Notification.permission),
    viewport: safe(() => innerWidth + 'x' + innerHeight),
    screen: safe(() => screen.width + 'x' + screen.height),
    devicePixelRatio: safe(() => devicePixelRatio),
    headlessUserAgent: safe(() => /headless/i.test(navigator.userAgent)),
    webglVendor: safe(() => {
      const gl = document.createElement('canvas').getContext('webgl');
      const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
      return info ? gl.getParameter(info.UNMASKED_VENDOR_WEBGL) : null;
    }),
    nativeToString: safe(() => /\[native code\]/.test(
      Function.prototype.toString.call(navigator.permissions.query)
    ))
  };

  // Globals left behind by drivers. Includes this project's own injected
  // objects, which are a signal we create ourselves.
  const driverGlobals = [
    '__playwright', '__pw_manual', '__puppeteer_evaluation_script__',
    '__selenium_evaluate', '__webdriver_evaluate', '__driver_evaluate',
    'callSelenium', '_selenium', 'domAutomationController', 'domAutomation',
    'cdc_adoQpoasnfa76pfcZLmcfl_Array', 'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
    '__jevFast', '__jevCursor'
  ];
  findings.driverGlobals = safe(() => driverGlobals.filter((name) => name in window));

  // The documented Runtime.enable leak: with the Runtime domain enabled, the
  // browser serialises console arguments and reads a stack getter the page can
  // observe. Without Runtime.enable nothing reads it.
  findings.runtimeLeak = safe(() => {
    let touched = false;
    const probe = new Error('probe');
    Object.defineProperty(probe, 'stack', {
      configurable: true,
      get() {
        touched = true;
        return 'probe';
      }
    });
    console.debug(probe);
    return touched;
  });

  return findings;
})()
