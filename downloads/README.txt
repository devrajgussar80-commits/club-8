The Android app is normally uploaded from the ADMIN DASHBOARD (Security tab),
which stores it in the database and serves it at:

    <backend>/api/app/download

That is the recommended way -- it survives redeploys and does not bloat the
git repo. The account-page "Download Android App" button and the /download
page use that backend URL automatically.

This folder is only an optional manual fallback: if you drop a file here named
club8.apk and commit it, Vercel will also serve it at /downloads/club8.apk
with a forced-download header (see vercel.json). You do not need this if you
use the admin upload.
