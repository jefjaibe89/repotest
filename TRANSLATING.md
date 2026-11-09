# Adding a language

Both surfaces — the dashboard and the commercial landing page — ship in
English and switch to Spanish. Adding a third language touches one file in
each.

## The dashboard

Copy `sdwan_dashboard/i18n/locales/en.json` to the new language code and
translate the values:

```bash
cp sdwan_dashboard/i18n/locales/en.json sdwan_dashboard/i18n/locales/fr.json
```

That is the whole change. Catalogues are discovered at import, so the switcher
picks the language up on the next restart — there is no registration list, no
`.po` compilation and no build step.

Two things the file must carry:

- **`_meta`**, with `name` (English name, used in tooltips) and `native` (shown
  on the switcher button).
- **Every key English defines.** A missing key falls back to English rather
  than showing a raw identifier, so a partial translation still renders — but
  the test suite fails until it is complete, which is the point.

Keep `{placeholders}` exactly as they appear in English. `tunnels.summary` uses
`{up}`, `{down}` and `{total}`; renaming or dropping one is caught by
`test_placeholders_match_english`.

### How a language gets chosen

In order: `?lang=xx` in the URL, then the `dashboard_lang` cookie set by the
switcher, then the browser's `Accept-Language`, then English. A regional
variant matches its base language, so `es-AR` resolves to `es`.

### What is deliberately not translated

Values that come from vManage: device hostnames, interface names, TLOC colours,
protocol states, and the text of alarms the controller raises. Those are data,
not interface. Computed findings *are* translated — they carry a key and
parameters rather than a formatted sentence, with the English rendering kept as
a fallback for API consumers that ignore the key.

## The landing page

The landing page is static, with no server, so it carries both catalogues and
switches in the browser. Add an entry to the `LANG` object in
`landing/index.html`:

```js
var LANG = {
  en: { _native: "EN", _name: "English", _title: "Fabric Health", ... },
  es: { ... },
  fr: { _native: "FR", _name: "Français", _title: "Fabric Health", ... }
};
```

The switcher is built from `Object.keys(LANG)`, so the button appears on its
own. Initial language: a previous choice in `localStorage`, then the browser's
language, then English.

Text is applied with `textContent`, never `innerHTML` — the copy is data, and
none of it is markup.

## Running the checks

```bash
cd sdwan_dashboard && python -m pytest tests/test_i18n.py -v
```

Those tests are what keeps translations honest over time. They fail when a
catalogue is missing keys, defines keys English does not, has mismatched
placeholders, or is more than half identical to English — which is what a
copied-but-never-translated file looks like.
