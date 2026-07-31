// Backend base URL.
//
// On localhost this points at the local API instead of production. Hardcoding
// the deployed URL meant a local page called PythonAnywhere, which rejects
// localhost origins ("Disallowed CORS origin") and surfaces as "Failed to
// fetch" on every form. It also meant local testing wrote to the live database.
//
// The local API defaults to port 8080 (`uvicorn main:app --app-dir backend
// --port 8080`). Its FRONTEND_ORIGINS default already allows both localhost
// :3000 and :8080, so serving the frontend from either port works.
(function () {
  // The deployed API. Render names the service from render.yaml, so this is
  // https://<service-name>.onrender.com -- confirm it on the Render dashboard
  // after the first deploy, because Render appends a suffix if the name is
  // already taken globally. This one value is what points the Vercel frontend
  // at the new backend; nothing else in the app hardcodes a host.
  const PRODUCTION_API_URL = 'https://club-8-api.onrender.com';

  const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]', ''];
  const isLocal = LOCAL_HOSTS.includes(window.location.hostname);

  window.APP_CONFIG = Object.freeze({
    API_BASE_URL: isLocal ? 'http://localhost:8080' : PRODUCTION_API_URL
  });
})();
