// UI language. The code and the HTML are written in English; other languages are dictionaries
// from the English text to the translation (lang/*.js), so a missing entry just shows English.
// The choice is kept in localStorage and in a cookie, which the server reads for its messages.
const LANGS = { en: "English", pt: "Português (Brasil)" };
const I18N = {};  // lang -> {English text: translation}

const LANG = (() => {
  try {
    const saved = localStorage.getItem("lang");
    if (saved in LANGS) return saved;
  } catch {}
  return "en";
})();
document.documentElement.lang = LANG === "pt" ? "pt-BR" : "en";
document.cookie = `lang=${LANG}; path=/; max-age=31536000; SameSite=Lax`;

// t("Deleting {n} item(s)…", {n: 3})
function t(text, vars) {
  let s = (I18N[LANG] && I18N[LANG][text]) || text;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return s;
}

function setLang(lang) {
  try { localStorage.setItem("lang", lang); } catch {}
  document.cookie = `lang=${lang}; path=/; max-age=31536000; SameSite=Lax`;
  location.reload();
}

const norm = (s) => s.replace(/\s+/g, " ").trim();

// Translates the static HTML: elements marked data-i18n as a whole (their inner HTML, for text
// with markup inside), and otherwise text nodes and title/placeholder/aria-label attributes
// whose whole text is a dictionary entry.
function translateDom(root = document.body) {
  const dict = I18N[LANG];
  if (!dict) return;
  for (const el of root.querySelectorAll("[data-i18n]")) {
    const html = dict[norm(el.innerHTML)];
    if (html) el.innerHTML = html;
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = norm(node.nodeValue);
    if (text && dict[text] && !node.parentElement.closest("[data-i18n]")) {
      node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), dict[text]);
    }
  }
  for (const el of root.querySelectorAll("[title],[placeholder],[aria-label]")) {
    for (const attr of ["title", "placeholder", "aria-label"]) {
      const v = el.getAttribute(attr);
      if (v && dict[norm(v)]) el.setAttribute(attr, dict[norm(v)]);
    }
  }
}
