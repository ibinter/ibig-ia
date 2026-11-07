/*
 * SARA — bulle de discussion pour les sites des solutions IBIG.
 *
 * <script src="/sara/sara-widget.js" data-relay="/sara/sara-relay.php"
 *         data-title="SARA" data-color="#1f6feb" defer></script>
 *
 * Aucune clé ici : le widget parle uniquement au relais du site (sara-relay.php).
 * Aucune dépendance. Le texte reçu est affiché avec textContent (jamais en HTML).
 */
(function () {
  'use strict';
  var script = document.currentScript;
  if (!script || window.__saraWidget) return;
  window.__saraWidget = true;

  var relay = script.getAttribute('data-relay') || '/sara/sara-relay.php';
  var title = script.getAttribute('data-title') || 'SARA';
  var color = script.getAttribute('data-color') || '#1f6feb';
  var hello = script.getAttribute('data-hello') ||
    "Bonjour, je suis SARA. Posez-moi votre question sur nos solutions.";

  var conversation = '';
  try {
    conversation = sessionStorage.getItem('sara-conv') || '';
    if (!conversation) {
      conversation = 'c' + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
      sessionStorage.setItem('sara-conv', conversation);
    }
  } catch (e) { conversation = 'c' + Math.random().toString(36).slice(2, 14); }

  function el(tag, props, children) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === 'style') n.style.cssText = props[k]; else if (k === 'text') n.textContent = props[k];
      else n.setAttribute(k, props[k]);
    });
    (children || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }

  var css = document.createElement('style');
  css.textContent =
    '#sara-btn{position:fixed;right:18px;bottom:18px;width:56px;height:56px;border-radius:50%;border:0;' +
    'background:' + color + ';color:#fff;font-size:26px;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);z-index:2147483000}' +
    '#sara-box{position:fixed;right:18px;bottom:86px;width:min(370px,calc(100vw - 24px));height:min(520px,calc(100vh - 110px));' +
    'background:#fff;color:#1c2330;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;flex-direction:column;' +
    'overflow:hidden;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;z-index:2147483000}' +
    '#sara-box.open{display:flex}#sara-head{background:' + color + ';color:#fff;padding:12px 14px;font-weight:600;display:flex;justify-content:space-between;align-items:center}' +
    '#sara-head button{background:none;border:0;color:#fff;font-size:20px;cursor:pointer}' +
    '#sara-log{flex:1;overflow:auto;padding:12px;background:#f5f7fb}' +
    '.sara-m{max-width:86%;margin:0 0 8px;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}' +
    '.sara-u{margin-left:auto;background:' + color + ';color:#fff;border-bottom-right-radius:3px}' +
    '.sara-b{background:#fff;border:1px solid #e1e6ef;border-bottom-left-radius:3px}' +
    '.sara-s{font-size:12px;color:#5b6577;margin:-4px 0 8px 4px}' +
    '#sara-contact{border:0;border-top:1px solid #e1e6ef;padding:7px 12px;font:inherit;font-size:12px;width:100%;box-sizing:border-box}' +
    '#sara-form{display:flex;border-top:1px solid #e1e6ef}' +
    '#sara-in{flex:1;border:0;padding:12px;font:inherit;outline:0;min-width:0}' +
    '#sara-send{border:0;background:' + color + ';color:#fff;padding:0 16px;cursor:pointer;font:inherit}' +
    '#sara-send[disabled]{opacity:.5;cursor:default}';
  document.head.appendChild(css);

  var log = el('div', { id: 'sara-log', 'aria-live': 'polite' });
  var input = el('input', { id: 'sara-in', type: 'text', maxlength: '2000', placeholder: 'Votre question…', 'aria-label': 'Votre question' });
  var send = el('button', { id: 'sara-send', type: 'submit', text: 'Envoyer' });
  var contact = el('input', { id: 'sara-contact', type: 'text', maxlength: '300', autocomplete: 'email',
    placeholder: 'Votre mail ou téléphone (facultatif, pour vous recontacter)', 'aria-label': 'Votre mail ou téléphone' });
  var close = el('button', { type: 'button', 'aria-label': 'Fermer', text: '×' });
  var box = el('div', { id: 'sara-box', role: 'dialog', 'aria-label': 'Discussion avec ' + title }, [
    el('div', { id: 'sara-head' }, [el('span', { text: title }), close]),
    log, contact, el('form', { id: 'sara-form' }, [input, send])
  ]);
  var btn = el('button', { id: 'sara-btn', type: 'button', 'aria-label': 'Discuter avec ' + title, text: '💬' });
  document.body.appendChild(box); document.body.appendChild(btn);

  function say(text, who, sources) {
    log.appendChild(el('div', { 'class': 'sara-m ' + (who === 'u' ? 'sara-u' : 'sara-b'), text: text }));
    if (sources && sources.length) {
      log.appendChild(el('div', { 'class': 'sara-s',
        text: 'Sources : ' + sources.map(function (s) { return s.titre + (s.section ? ' — ' + s.section : ''); }).join(' · ') }));
    }
    log.scrollTop = log.scrollHeight;
  }

  function toggle(open) {
    box.classList.toggle('open', open);
    if (open) { if (!log.children.length) say(hello, 'b'); input.focus(); }
  }
  btn.addEventListener('click', function () { toggle(!box.classList.contains('open')); });
  close.addEventListener('click', function () { toggle(false); });

  box.querySelector('form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    say(text, 'u'); input.value = ''; send.disabled = true;
    fetch(relay, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, conversation: conversation, contact: contact.value.trim() })
    }).then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (d.erreur) { say(d.erreur, 'b'); return; }
        say(d.reponse || 'Je transmets votre question à un conseiller.', 'b', d.sources);
        if (d.transmis && d.ticket) say('Votre demande porte le numéro ' + d.ticket + '.', 'b');
      })
      .catch(function () { say("Je n'arrive pas à répondre pour le moment : un conseiller vous répondra.", 'b'); })
      .then(function () { send.disabled = false; input.focus(); });
  });
})();
