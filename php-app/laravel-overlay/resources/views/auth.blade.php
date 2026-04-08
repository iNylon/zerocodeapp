<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Northstar Market | Inloggen</title>
  <style>
    body { margin:0; min-height:100vh; font-family:system-ui,sans-serif; background:linear-gradient(140deg, #f6efde, #d9ebe2); color:#19232c; display:grid; place-items:center; padding:14px; }
    .auth-shell { width:min(920px,100%); border:1px solid rgba(0,0,0,.1); border-radius:24px; background:rgba(255,255,255,.94); padding:20px; display:grid; gap:14px; }
    .top,.cols { display:grid; gap:12px; }
    .cols { grid-template-columns: 1fr 1fr; }
    form { border:1px solid rgba(0,0,0,.1); border-radius:14px; padding:14px; background:#fff; display:grid; gap:8px; }
    input,button,a { font:inherit; border-radius:10px; }
    input { border:1px solid rgba(0,0,0,.1); padding:10px 11px; }
    button { border:0; padding:10px 12px; cursor:pointer; font-weight:700; }
    .btn-register { background:#b85d15; color:#fff; } .btn-login { background:#1f685a; color:#fff; }
    .home-link { text-decoration:none; font-weight:700; color:#fff; background:#19232c; padding:8px 12px; }
    .status { border:1px dashed rgba(0,0,0,.16); border-radius:12px; padding:10px; background:#fdfdfd; }
    @media (max-width: 860px) { .cols { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <main class="auth-shell">
    <div class="top">
      <h1>Inloggen of registreren</h1>
      <a class="home-link" href="/">Terug naar shop</a>
    </div>
    <p style="margin:0;color:rgba(25,35,44,.82)">Na inloggen kun je meteen producten bestellen.</p>
    <section class="cols">
      <form id="register-form">
        <strong>Nieuw account</strong>
        <label for="register-email">E-mailadres</label>
        <input id="register-email" type="email" required placeholder="jij@voorbeeld.nl">
        <label for="register-password">Wachtwoord</label>
        <input id="register-password" type="password" minlength="8" required placeholder="Minimaal 8 tekens">
        <button class="btn-register" type="submit">Account aanmaken</button>
      </form>
      <form id="login-form">
        <strong>Bestaand account</strong>
        <label for="login-email">E-mailadres</label>
        <input id="login-email" type="email" required placeholder="jij@voorbeeld.nl">
        <label for="login-password">Wachtwoord</label>
        <input id="login-password" type="password" required placeholder="Wachtwoord">
        <button class="btn-login" type="submit">Inloggen</button>
      </form>
    </section>
    <div class="status" id="auth-status">Nog niet ingelogd.</div>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    const nextPath = params.get('next') || '/';
    const statusBox = document.getElementById('auth-status');
    function setStatus(message) { statusBox.textContent = message; }
    async function requestJson(path, method = 'GET', body) {
      const response = await fetch(path, {
        method,
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
      });
      let payload = {};
      try { payload = await response.json(); } catch (_error) {}
      if (!response.ok) throw new Error(payload && payload.error ? payload.error : `Request failed (${response.status})`);
      return payload;
    }
    async function refreshCurrentUser() {
      try {
        const payload = await requestJson('/api/me');
        if (payload.authenticated && payload.user) {
          setStatus(`Ingelogd als ${payload.user.email}. Je wordt nu doorgestuurd...`);
          setTimeout(() => { window.location.href = nextPath; }, 700);
        }
      } catch (_error) {}
    }
    document.getElementById('register-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = document.getElementById('register-email').value.trim();
      const password = document.getElementById('register-password').value;
      try { const payload = await requestJson('/api/register', 'POST', { email, password }); setStatus(`Account aangemaakt voor ${payload.user.email}.`); await refreshCurrentUser(); } catch (error) { setStatus(`Registreren mislukt: ${error.message}`); }
    });
    document.getElementById('login-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const email = document.getElementById('login-email').value.trim();
      const password = document.getElementById('login-password').value;
      try { const payload = await requestJson('/api/login', 'POST', { email, password }); setStatus(`Welkom terug, ${payload.user.email}.`); await refreshCurrentUser(); } catch (error) { setStatus(`Inloggen mislukt: ${error.message}`); }
    });
    refreshCurrentUser();
  </script>
</body>
</html>
