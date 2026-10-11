# Arcager 4.2 browser API

A packed page carries a small runtime that unpacks everything and then exposes a handful of
helpers on `window.arcager`. This document is the complete reference, with examples you can
copy. Everything below was verified against the real runtime.

| Member | Type | What it gives you |
|---|---|---|
| [`arcager.ready`](#readiness-and-errors) | `Promise` | Resolves when the package is unpacked; rejects if that fails. |
| [`arcager.error`](#readiness-and-errors) | `Error` or `null` | `null` on success, the failure otherwise. |
| [`arcager.images`](#images) | `object` | Map of name → `blob:` URL for **every** packaged resource. |
| [`arcager.getImage(name)`](#getimagename) | `function` | A new `Image` for a name, or `null`. |
| [`arcager.csv`](#csv-tables) | `object` | Map of table key → `{ rows, data }`. |

> **Packed pages only.** `window.arcager` exists only after packing. During development, when
> you open the unpacked sources directly, it is `undefined`. See
> [Working with and without Arcager](#working-with-and-without-arcager).

---

## Readiness and errors

Always wait for `ready` before using `images` or `csv`:

```javascript
try {
    await window.arcager.ready;
    startApp();
} catch (error) {
    // `window.arcager.error` is the same Error that made `ready` reject.
    document.body.textContent = "Could not load: " + error.message;
}
```

`ready` rejects when the package is damaged, the browser lacks `DecompressionStream`, the
password prompt of an encrypted package is cancelled, and similar failures. The unpacking
panel that Arcager shows on failure already displays the message; handle the rejection if you
want your own UI.

Old v3 signals (`arcager.state`, `arcager.loaded`) no longer exist.

---

## Images

### `arcager.images`

An object whose keys are resource names and whose values are `blob:` URLs. Despite the name it
contains **every** resource in the package: images, but also fonts, CSS, JavaScript, CSV files
and `index.html`. Filter by extension when you only want pictures (see
[Gallery](#example-a-gallery-of-all-packaged-images)).

Each resource is registered under up to three names, derived from its path in the package:

| Package path | Names registered |
|---|---|
| `img/logo.png` | `img/logo.png`, `img/logo`, `logo` |
| `photo.jpg` | `photo.jpg`, `photo` |

Rules worth knowing:

* **The short name wins once.** If two resources share a short name (`css/app.css` and
  `js/app.js` are both `app`), only the first one gets `app`. Use the full path for the other.
* **Names are case-sensitive in `images`**, and a leading `./` or `/` is not accepted there.
  [`getImage()`](#getimagename) is more forgiving.
* **Files added with `-b PATH` have no extension in their name.** A bundle file `img/logo.png`
  is `img/logo` and `logo`, not `logo.png`. `getImage("logo.png")` still works because it
  retries without the extension.
* **Identical files share one URL.** If `a.svg` and `b.svg` have the same bytes they are stored
  once and `images["a.svg"] === images["b.svg"]`.
* **Stored form.** If you packed with `lossy` and a PNG was converted to WebP, the URL serves the
  WebP. Browsers display it identically.
* Missing names are simply `undefined`.

The URLs are valid for the lifetime of the page. They cannot be shared with other tabs or
pages, and are not stable between loads, so never save one.

### `getImage(name)`

Returns a **new** `Image` element whose `src` is the packaged resource, or `null` if nothing
matches. It tries, in order: the name as given, then without a leading `./` or `/`, then
without a file extension, each time also in lower case. So all of these find `img/logo.png`:

```javascript
getImage("img/logo.png");   getImage("./img/logo.png");   getImage("IMG/LOGO.PNG");
getImage("img/logo");       getImage("logo");             getImage("logo.png");
```

Each call creates a fresh element. Decoding is asynchronous, so wait for `load` before reading
`width`/`height` or drawing it to a canvas.

### Example: show an image

```javascript
await window.arcager.ready;

const logo = window.arcager.getImage("logo.png");
if (logo) {
    logo.alt = "Company logo";
    document.querySelector("header").appendChild(logo);
} else {
    console.warn("logo.png is not in this package");
}
```

### Example: draw on a canvas

```javascript
await window.arcager.ready;

const img = window.arcager.getImage("sprites/hero");
await img.decode();                       // resolves when the image is ready to draw
const ctx = document.querySelector("canvas").getContext("2d");
ctx.drawImage(img, 0, 0);
```

### Example: use a URL in CSS or an attribute

```javascript
await window.arcager.ready;

const url = window.arcager.images["img/texture.png"];
if (url) document.body.style.backgroundImage = `url(${url})`;
```

Plain markup needs none of this: an `<img src="img/logo.png">` or `url(img/logo.png)` that
existed in your source is rewritten for you at pack time. The API is for images you pick
**at run time** (names built in code, user choices, resources not referenced in the HTML).

### Example: a gallery of all packaged images

```javascript
await window.arcager.ready;

const isPicture = name => /\.(png|jpe?g|webp|gif|avif|svg)$/i.test(name);
const gallery = document.querySelector("#gallery");

for (const name of Object.keys(window.arcager.images)) {
    // Skip the short aliases ("logo") so each picture appears once.
    if (!isPicture(name)) continue;
    const img = window.arcager.getImage(name);
    img.title = name;
    img.loading = "lazy";
    gallery.appendChild(img);
}
```

Files added with `-b` are registered without an extension, so this filter does not see them.
List those by name instead (for example `["photos/a", "photos/b"]`).

### Example: read a packaged text file

Because every resource has a URL, you can fetch non-image files too:

```javascript
await window.arcager.ready;

const response = await fetch(window.arcager.images["data/notes.txt"]);
const text = await response.text();
```

(CSV is easier through [`arcager.csv`](#csv-tables).)

---

## CSV tables

### Where tables come from

| In your source | Key in `arcager.csv` |
|---|---|
| `<link rel="csv" href="data/elements.csv">` packed with `-M` | `elements` (the file name without extension, unsafe characters replaced by `_`) |
| Any `<link href="…csv">` or `.tsv` packed with `-M` | the same rule |
| `<script type="text/csv" data-key="prices">…</script>` in your HTML | `prices` |
| `<script type="text/csv">…</script>` without `data-key` | `csv0`, `csv1`, … by position among the CSV blocks |

If two linked files have the same name (`a/data.csv` and `b/data.csv`), Arcager warns while
packing and exposes the second as `data_2`. Add `data-key` or rename a file to choose the key
yourself. Linking the same file twice stores it once.

A table linked with `<link rel="csv">` is stored **once** in the package, inline in the page.
`arcager -l` shows it once with its original path, and `arcager -x csv` writes the original
file back byte for byte.

### `arcager.csv[key]`

```javascript
const table = window.arcager.csv.elements;   // undefined if the key does not exist

table.rows;  // array of arrays. rows[0] is the header row.
table.data;  // array of objects keyed by the header, one per data row. Header excluded.
```

For this file:

```text
name,qty,note
Widget,3,"has, comma"
Gadget,5,"says ""hi"""
```

```javascript
table.rows;
// [["name","qty","note"], ["Widget","3","has, comma"], ["Gadget","5","says \"hi\""]]
table.data;
// [{name:"Widget", qty:"3", note:"has, comma"}, {name:"Gadget", qty:"5", note:'says "hi"'}]
```

### Parsing rules

* **Every value is a string.** Convert numbers and dates yourself (`Number(row.qty)`).
* Fields may be quoted with `"`. A quoted field can contain the delimiter, doubled quotes
  (`""` → `"`) and **line breaks**.
* The delimiter is a **tab** if the first line contains a tab, otherwise a **comma**. So `.tsv`
  files work without configuration. One delimiter is used for the whole table.
* `\r\n`, `\n` and `\r` line endings and a leading byte-order mark are handled.
* **Blank lines are skipped.**
* In `data`, a short row is padded with `""`; cells beyond the header are not in `data` (they
  remain in `rows`).
* An empty table gives `rows: []`, `data: []`. A header-only table gives `data: []`.
* Duplicate header names: the later column wins in `data`; use `rows` if you need all of them.

### Example: render a table

```javascript
await window.arcager.ready;

const { rows } = window.arcager.csv.elements;
const [header, ...body] = rows;

const table = document.createElement("table");
table.innerHTML =
    "<thead><tr>" + header.map(h => `<th>${h}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + body.map(r => "<tr>" + r.map(c => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody>";
document.body.appendChild(table);
```

(Escape cell text if the data is not yours: use `textContent`, not `innerHTML`.)

### Example: look up, filter, convert

```javascript
await window.arcager.ready;

const elements = window.arcager.csv.elements.data;

const hydrogen = elements.find(e => e.sym === "H");
const heavy    = elements.filter(e => Number(e.mass) > 200)
                         .sort((a, b) => Number(b.mass) - Number(a.mass));
const metals   = elements.filter(e => /metal/.test(e.cat)).length;
```

### Example: join two tables

```javascript
await window.arcager.ready;

const { elements, "element-extra": extra } = window.arcager.csv;   // keys may contain "-"
const byZ = new Map(extra.data.map(r => [r.z, r]));
const combined = elements.data.map(e => ({ ...e, ...byZ.get(e.z) }));
```

### Example: a missing or optional table

```javascript
await window.arcager.ready;

function table(key) {
    const t = window.arcager.csv[key];
    if (!t) throw new Error(
        `Table "${key}" is not in this package. Available: ${Object.keys(window.arcager.csv).join(", ") || "none"}`);
    return t;
}
```

### Example: all tables at once

```javascript
await window.arcager.ready;

for (const [key, { rows, data }] of Object.entries(window.arcager.csv)) {
    console.log(`${key}: ${data.length} rows, columns: ${rows[0].join(", ")}`);
}
```

### Limitation

Text inside a table that looks like a closing script tag is stored escaped
(`<\/script>`) so it cannot end the page's `<script>` block. `arcager -x csv` and `-u` restore
the original text exactly, but `arcager.csv` in the running page shows the escaped form. Real
data almost never contains that text.

---

## Working with and without Arcager

While developing you usually open the unpacked sources, where `window.arcager` does not
exist and `<link rel="csv">` is not loaded by the browser. Write the loading code once so it
works in both situations:

```javascript
async function loadTable(key, path) {
    if (window.arcager) {                       // packed page
        await window.arcager.ready;
        return window.arcager.csv[key];         // { rows, data }
    }
    const text = await (await fetch(path)).text();   // development, from a local web server
    return parseYourOwnWay(text);
}

const elements = await loadTable("elements", "data/elements.csv");
```

If you want the data without any Arcager API at all, the packed page also contains the tables as
ordinary elements, which you can read directly:

```javascript
const raw = document.querySelector('script[type="text/csv"][data-key="elements"]').textContent;
```

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `window.arcager` is `undefined` | The page is not packed (or you are reading it before the packed page replaced itself). Pack it, and use `await window.arcager.ready`. |
| `arcager.csv.mytable` is `undefined` | Wrong key. Run `arcager -l csv packed.html`: the note after each table says `exposed as arcager.csv.<key>`. |
| `getImage()` returns `null` | The name is not in the package. Run `arcager -l packed.html`; for `-b` bundles remember the extension is not part of the name. |
| `images["logo.png"]` is `undefined` but `getImage("logo.png")` works | `images` needs the exact registered name (see the table above). Prefer `getImage()`. |
| Two files have the same short name | Only the first gets it. Use the full path. |
| Numbers compare as text | CSV values are strings. Use `Number(value)`. |
| Page is blank with an error panel | Open the browser console. Most often the browser is too old for `DecompressionStream` (needs a current Chrome, Edge, Firefox or Safari), or the encrypted package's password prompt was cancelled. |
