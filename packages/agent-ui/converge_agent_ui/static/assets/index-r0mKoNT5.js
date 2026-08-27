(function(){let e=document.createElement(`link`).relList;if(e&&e.supports&&e.supports(`modulepreload`))return;for(let e of document.querySelectorAll(`link[rel="modulepreload"]`))n(e);new MutationObserver(e=>{for(let t of e)if(t.type===`childList`)for(let e of t.addedNodes)e.tagName===`LINK`&&e.rel===`modulepreload`&&n(e)}).observe(document,{childList:!0,subtree:!0});function t(e){let t={};return e.integrity&&(t.integrity=e.integrity),e.referrerPolicy&&(t.referrerPolicy=e.referrerPolicy),t.credentials=e.crossOrigin===`use-credentials`?`include`:e.crossOrigin===`anonymous`?`omit`:`same-origin`,t}function n(e){if(e.ep)return;e.ep=!0;let n=t(e);fetch(e.href,n)}})();var e=document.querySelector(`#app`);if(e===null)throw Error(`Harness UI root element is missing`);e.innerHTML=`
  <section class="shell" aria-labelledby="page-title">
    <p class="eyebrow">Converge Agent UI</p>
    <h1 id="page-title">Local agent interaction, one shared stream.</h1>
    <p class="summary">
      This private WebUI is bundled into the <code>converge-agent-ui</code>
      Python distribution. WebUI and TUI share the same application service
      and Agent Stream Protocol projection.
    </p>
  </section>
`;
