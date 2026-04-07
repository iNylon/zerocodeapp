<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Northstar Market | Storefront</title>
  <style>
    body { margin:0; font-family: system-ui, sans-serif; background:#f6efde; color:#19232c; }
    .shell { max-width: 1240px; margin: 0 auto; padding: 24px; display:grid; gap:16px; }
    .hero,.panel { background:#fff; border:1px solid rgba(0,0,0,.08); border-radius:20px; padding:20px; }
    .hero-top,.toolbar,.row,.hero-actions { display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
    .layout { display:grid; grid-template-columns: minmax(0, 1.5fr) minmax(320px, .9fr); gap:16px; }
    .grid,.fault-grid,.orders-list { display:grid; gap:10px; }
    .grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .fault-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .sku,.section-box,.log { border:1px solid rgba(0,0,0,.08); border-radius:14px; padding:12px; background:#fff; }
    .sku img { width:100%; height:160px; object-fit:cover; border-radius:10px; }
    button,input,select { font: inherit; border-radius:10px; }
    button { border:0; padding:10px 12px; cursor:pointer; }
    .btn-main,.btn-sub { background:#19232c; color:#fff; }
    .btn-clear,.hero-link { background:#eef2f5; color:#19232c; text-decoration:none; padding:10px 12px; border-radius:10px; }
    .fault-button { background:#fff; border:1px solid rgba(0,0,0,.08); text-align:left; }
    .alert-button { background:#dc2626; color:#fff; }
    .tech-icon { width:32px; height:32px; border-radius:10px; display:inline-flex; align-items:center; justify-content:center; color:#fff; font-weight:700; margin-right:10px; }
    .mysql { background:#0a88a8; } .postgres { background:#4b7ed1; } .redis { background:#d82c20; } .php { background:#8892bf; } .python { background:#ffd43b; color:#13233a; } .java { background:#f2a348; } .nodejs { background:#68a063; } .alert { background:#ef4444; }
    .toolbar input,.toolbar select { padding:10px 12px; border:1px solid rgba(0,0,0,.1); }
    .toolbar input { flex:1 1 260px; }
    .kpi { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:8px; }
    .kpi div,.order-card { border:1px solid rgba(0,0,0,.08); border-radius:12px; padding:10px; background:#fff; }
    .log { min-height:180px; max-height:240px; overflow:auto; background:#0f1720; color:#e4ecf4; font-family: ui-monospace, monospace; }
    @media (max-width: 980px) { .layout { grid-template-columns:1fr; } .fault-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-top">
        <div>
          <h1>Northstar Market</h1>
          <p>Laravel storefront voor zero-code observability demo.</p>
        </div>
        <div class="hero-actions">
          <span id="hero-user">Niet ingelogd</span>
          <a class="hero-link" id="auth-link" href="/auth">Inloggen / Registreren</a>
          <button class="btn-clear" id="btn-logout" hidden>Uitloggen</button>
        </div>
      </div>
    </section>

    <section class="layout">
      <article class="panel">
        <div class="toolbar">
          <input id="search" placeholder="Zoek product, categorie of sku">
          <select id="category">
            <option value="all">Alle categorieen</option>
            <option value="apparel">Apparel</option>
            <option value="accessories">Accessories</option>
            <option value="stationery">Stationery</option>
          </select>
        </div>
        <div id="grid" class="grid"></div>
      </article>

      <aside class="panel">
        <h2>Winkelwagen</h2>
        <div class="kpi">
          <div><strong id="kpi-items">0</strong><div>Items</div></div>
          <div><strong id="kpi-subtotal">0.00</strong><div>Subtotal</div></div>
          <div><strong id="kpi-lat">-</strong><div>API ms</div></div>
        </div>

        <div class="section-box">
          <p>Cart</p>
          <div id="cart"></div>
        </div>

        <div style="margin:12px 0;">
          <button class="btn-sub" id="btn-checkout">Bestelling afronden</button>
        </div>

        <div class="section-box">
          <p>Storingen simuleren</p>
          <div class="fault-grid" id="fault-grid">
            <button class="fault-button" data-fault="mysql"><span class="tech-icon mysql">My</span><span>Trigger MySQL fout</span></button>
            <button class="fault-button" data-fault="postgres"><span class="tech-icon postgres">PG</span><span>Trigger PostgreSQL fout</span></button>
            <button class="fault-button" data-fault="redis"><span class="tech-icon redis">R</span><span>Trigger Redis fout</span></button>
            <button class="fault-button" data-fault="php"><span class="tech-icon php">PHP</span><span>Trigger PHP fout</span></button>
            <button class="fault-button" data-fault="python"><span class="tech-icon python">Py</span><span>Trigger Python fout</span></button>
            <button class="fault-button" data-fault="java"><span class="tech-icon java">J</span><span>Trigger Java fout</span></button>
            <button class="fault-button" data-fault="nodejs"><span class="tech-icon nodejs">JS</span><span>Trigger NodeJS fout</span></button>
            <button class="fault-button alert-button" id="btn-generate-alert"><span class="tech-icon alert">!</span><span>Genereer alert</span></button>
          </div>
        </div>

        <div class="section-box">
          <p>Mijn bestellingen</p>
          <p id="orders-meta">Log in om je bestellingen te zien.</p>
          <ul class="orders-list" id="orders"><li>Log in om je bestellingen te zien.</li></ul>
        </div>

        <div class="log" id="log">Start webshop-simulatie: voeg producten toe en rond een bestelling af.</div>
      </aside>
    </section>
  </div>

  <script>
    const products = [
      { sku: 'SKU-100', name: 'PHP Hoodie', category: 'apparel', price: 59.99, desc: 'Warm en zacht, met mini-logo op de mouw.', image: 'https://picsum.photos/id/1011/640/440' },
      { sku: 'SKU-101', name: 'Node Mug', category: 'accessories', price: 12.49, desc: 'Keramische mok voor deploy-dagen.', image: 'https://picsum.photos/id/1062/640/440' },
      { sku: 'SKU-102', name: 'Python Notebook', category: 'stationery', price: 9.95, desc: 'Lijnpapier voor design en SQL-notes.', image: 'https://picsum.photos/id/180/640/440' },
      { sku: 'SKU-103', name: 'Java Sticker Pack', category: 'accessories', price: 4.99, desc: 'Retro stickers voor je laptop.', image: 'https://picsum.photos/id/30/640/440' },
      { sku: 'SKU-104', name: 'OTEL Cap', category: 'apparel', price: 19.99, desc: 'Lichte cap met trace icon.', image: 'https://picsum.photos/id/64/640/440' },
      { sku: 'SKU-105', name: 'Redis Socks', category: 'apparel', price: 14.95, desc: 'Snelle voeten voor snelle cache hits.', image: 'https://picsum.photos/id/21/640/440' },
    ];
    const cart = new Map();
    let currentUser = null;
    const grid = document.getElementById('grid');
    const cartBox = document.getElementById('cart');
    const ordersBox = document.getElementById('orders');
    const ordersMeta = document.getElementById('orders-meta');
    const logBox = document.getElementById('log');
    const heroUser = document.getElementById('hero-user');
    const authLink = document.getElementById('auth-link');
    const logoutButton = document.getElementById('btn-logout');
    const checkoutButton = document.getElementById('btn-checkout');
    const kpiItems = document.getElementById('kpi-items');
    const kpiSubtotal = document.getElementById('kpi-subtotal');
    const kpiLat = document.getElementById('kpi-lat');
    const searchInput = document.getElementById('search');
    const categoryInput = document.getElementById('category');
    const faultButtons = [...document.querySelectorAll('button[data-fault]')];
    const alertButton = document.getElementById('btn-generate-alert');

    function euro(value) { return Number(value).toFixed(2); }
    function appendLog(title, payload) {
      const stamp = new Date().toISOString();
      const lines = [`[${stamp}] ${title}`];
      if (payload !== undefined) lines.push(JSON.stringify(payload, null, 2));
      logBox.textContent = `${lines.join('\n')}\n\n${logBox.textContent}`.slice(0, 9000);
    }
    function totalItems() { let amount = 0; for (const qty of cart.values()) amount += qty; return amount; }
    function subtotal() { let total = 0; for (const item of products) total += (cart.get(item.sku) || 0) * item.price; return total; }
    function cartPayload() { return [...cart.entries()].filter(([, qty]) => qty > 0).map(([sku, quantity]) => ({ sku, quantity })); }
    function setKpis(latencyMs) { kpiItems.textContent = String(totalItems()); kpiSubtotal.textContent = euro(subtotal()); if (typeof latencyMs === 'number') kpiLat.textContent = String(Math.round(latencyMs)); }
    function renderCart() {
      const entries = cartPayload();
      if (!entries.length) { cartBox.innerHTML = '<p>Winkelwagen is leeg.</p>'; setKpis(); return; }
      cartBox.innerHTML = entries.map((entry) => {
        const item = products.find((product) => product.sku === entry.sku);
        return `<div class="order-card"><strong>${item.name}</strong><div>${entry.quantity}x • EUR ${euro(item.price * entry.quantity)}</div></div>`;
      }).join('');
      setKpis();
    }
    function renderGrid() {
      const term = searchInput.value.trim().toLowerCase();
      const category = categoryInput.value;
      const filtered = products.filter((item) => (category === 'all' || item.category === category) && (term === '' || `${item.name} ${item.category} ${item.sku}`.toLowerCase().includes(term)));
      grid.innerHTML = filtered.map((item) => `
        <article class="sku">
          <img class="sku-img" src="${item.image}" alt="${item.name}">
          <div class="sku-body">
            <div class="row"><h3>${item.name}</h3><span>${item.category}</span></div>
            <p>${item.desc}</p>
            <div class="row"><strong>EUR ${euro(item.price)}</strong><button class="btn-main" data-sku="${item.sku}">Toevoegen</button></div>
          </div>
        </article>`).join('');
      grid.querySelectorAll('button[data-sku]').forEach((button) => {
        button.addEventListener('click', () => {
          const sku = button.getAttribute('data-sku');
          cart.set(sku, (cart.get(sku) || 0) + 1);
          renderCart();
          appendLog('cart:add', { sku, qty: cart.get(sku) });
        });
      });
    }
    async function requestJson(path, method = 'GET', body) {
      const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      let payload = {};
      try { payload = await response.json(); } catch (_error) {}
      if (!response.ok) throw new Error(payload && payload.error ? payload.error : `Request failed (${response.status})`);
      return { payload, status: response.status };
    }
    function renderOrders(orders) {
      if (!currentUser) { ordersMeta.textContent = 'Log in om je bestellingen te zien.'; ordersBox.innerHTML = '<li>Log in om je bestellingen te zien.</li>'; return; }
      if (!orders || !orders.length) { ordersMeta.textContent = 'Nog geen bestellingen geplaatst.'; ordersBox.innerHTML = '<li>Nog geen bestellingen geplaatst.</li>'; return; }
      ordersMeta.textContent = `${orders.length} bestellingen geladen. Nieuwste bovenaan.`;
      ordersBox.innerHTML = orders.map((order) => `<li class="order-card"><div class="row"><strong>${order.order_id}</strong><span>${order.status}</span></div><p>EUR ${euro(order.total_amount)} • ${new Date(order.created_at).toLocaleString()} • ${order.items.length} regels</p><div>${order.items.map((item) => `${item.quantity}x ${item.name} (${item.sku})`).join(' • ')}</div></li>`).join('');
    }
    function applyAuthState(data) {
      if (!data || !data.authenticated || !data.user) {
        currentUser = null; heroUser.textContent = 'Niet ingelogd'; authLink.hidden = false; logoutButton.hidden = true; checkoutButton.disabled = false; checkoutButton.title = 'Log in om je bestelling af te ronden'; renderOrders([]); return;
      }
      currentUser = data.user; heroUser.textContent = `Ingelogd als ${data.user.email}`; authLink.hidden = true; logoutButton.hidden = false; checkoutButton.disabled = false; checkoutButton.title = '';
    }
    async function refreshAuthState() { try { const { payload } = await requestJson('/api/me'); applyAuthState(payload); } catch (error) { appendLog('auth:state-failed', { error: error.message }); } }
    async function refreshOrders() { if (!currentUser) { renderOrders([]); return; } try { const { payload } = await requestJson('/api/orders'); renderOrders(payload.orders || []); } catch (error) { appendLog('orders:load-failed', { error: error.message }); } }
    async function triggerFault(target, button) { const original = button.innerHTML; button.disabled = true; try { const { payload } = await requestJson(`/api/fault/${target}`, 'POST'); appendLog('fault:triggered', payload); } catch (error) { appendLog('fault:trigger-failed', { target, error: error.message }); } finally { button.disabled = false; button.innerHTML = original; } }
    async function triggerAlert(button) { const original = button.innerHTML; button.disabled = true; try { const { payload } = await requestJson('/api/alert', 'POST'); appendLog('alert:triggered', payload); } catch (error) { appendLog('alert:trigger-failed', { error: error.message }); } finally { button.disabled = false; button.innerHTML = original; } }
    async function checkoutOrder() {
      if (!currentUser) { appendLog('checkout:blocked', { reason: 'not-authenticated' }); window.location.href = '/auth?next=/'; return; }
      const items = cartPayload();
      if (!items.length) { appendLog('checkout:blocked', { reason: 'empty-cart' }); return; }
      const start = performance.now();
      try { const { payload } = await requestJson('/api/checkout', 'POST', { items }); setKpis(performance.now() - start); appendLog('checkout:order-confirmed', payload.order || {}); cart.clear(); renderCart(); await refreshOrders(); } catch (error) { setKpis(performance.now() - start); appendLog('checkout:failed', { error: error.message }); }
    }
    checkoutButton.addEventListener('click', checkoutOrder);
    faultButtons.forEach((button) => button.addEventListener('click', () => triggerFault(button.getAttribute('data-fault'), button)));
    alertButton.addEventListener('click', () => triggerAlert(alertButton));
    logoutButton.addEventListener('click', async () => { try { await requestJson('/api/logout', 'POST'); appendLog('auth:logout-success', {}); applyAuthState({ authenticated: false }); } catch (error) { appendLog('auth:logout-failed', { error: error.message }); } });
    searchInput.addEventListener('input', renderGrid);
    categoryInput.addEventListener('change', renderGrid);
    renderGrid(); renderCart(); refreshAuthState().then(() => refreshOrders());
  </script>
</body>
</html>
