(() => {
  // A visible pointer, drawn by the page itself so it appears in screenshots and
  // recordings. Real DOM events drive it: the synthetic mouse events the agent
  // already dispatches are indistinguishable to these listeners.
  //
  // It is always injected and deliberately anonymous: it creates no global. A
  // window property named after this project is exactly the shape of a driver
  // leftover, and the detection probe is right to flag one.
  const attach = () => {
    if (!document.body || document.querySelector('jev-cursor')) return;

    const style = document.createElement('style');
    style.textContent = [
      'jev-cursor{pointer-events:none;position:fixed;top:0;left:0;z-index:2147483647;',
      'width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;',
      'background:rgba(15,23,20,.62);border:1.5px solid #fff;',
      'box-shadow:0 1px 6px rgba(0,0,0,.35);',
      'transition:background .15s ease,transform .08s ease}',
      'jev-cursor[data-pressed="true"]{background:rgba(23,106,72,.95);transform:scale(.82)}',
      'jev-cursor[data-hidden="true"]{display:none}'
    ].join('');

    const node = document.createElement('jev-cursor');
    node.setAttribute('data-pressed', 'false');
    // Hidden until the pointer actually moves, so it does not sit in the corner.
    node.setAttribute('data-hidden', 'true');

    const place = (event) => {
      node.style.left = event.clientX + 'px';
      node.style.top = event.clientY + 'px';
      node.removeAttribute('data-hidden');
    };

    document.head.appendChild(style);
    document.body.appendChild(node);

    document.addEventListener('mousemove', place, true);
    document.addEventListener('mousedown', (event) => {
      place(event);
      node.setAttribute('data-pressed', 'true');
    }, true);
    document.addEventListener('mouseup', (event) => {
      place(event);
      node.setAttribute('data-pressed', 'false');
    }, true);
    // `mouseleave` fires for every element the pointer leaves and a capture-phase
    // listener on the document sees all of them, so moving across an ordinary
    // page hid the pointer at random. Only hide it when it leaves the window.
    document.addEventListener('mouseout', (event) => {
      if (!event.relatedTarget) node.setAttribute('data-hidden', 'true');
    }, true);
  };

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', attach, false);
  } else {
    attach();
  }
})()
