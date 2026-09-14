"""Strip CSS rules from an EPUB that can never match anything in that book's
actual content.

Root cause (confirmed via koreader/koreader#14021 -- filed against
jw.org's own es25_E.epub Daily Text -- and crengine's maintainer diagnosing
that exact issue): jw.org bundles their entire site-wide CSS framework,
including a complete icon-font glyph map, into every EPUB rather than
trimming it to what that specific publication uses. A real jw.org Daily
Text book carries an 18,784-rule, 1.67MB stylesheet of which only ~1,400
rules (~8%) are ever used.

This is not just dead weight -- it makes KOReader's crengine pathologically
slow to open the book. Read directly from crengine's source
(lvstsheet.cpp): `LVStyleSheet::apply()` buckets selectors by their
rightmost tag name, but any selector with no tag qualifier (which is what
virtually all of these unused class-only icon rules are, e.g.
".jwf-jw-icons-all.jwi-airplay:before") falls into one shared bucket that
is walked *in full, as a linked list, for every single node in the
document*. With ~17,000 such rules and tens of thousands of elements across
a several-hundred-file book, that's on the order of hundreds of millions of
comparisons -- which matches the exact real-world report in #14021 ("3
minutes on an emulator... probably dozens of minutes on a device") far
better than crengine's separate, more generic "big book" layout cost does.

Unlike the CrossPoint optimizer (image resize/grayscale, paragraph/chapter
splitting -- all irrelevant here, and paragraph-splitting in particular
would make crengine's per-node cost *worse* by increasing the node count),
this transformation only removes CSS rules that are provably unreachable
given the book's own markup, and is resolution/device-independent -- it
helps any crengine-based reader (KOReader on Kobo/Kindle/Android/desktop),
not one specific hardware profile.
"""
from __future__ import annotations

import re
import zipfile

import tinycss2

CLASS_OR_ID_RE = re.compile(r'[.#][A-Za-z0-9_-]+')

# :not(...)/:is(...)/:where(...) arguments are NOT positive requirements --
# e.g. ".foo:not(.bar)" matches any .foo that ISN'T also .bar, so ".bar"
# being absent from the document doesn't make the selector unmatchable (if
# anything, it trivially satisfies the :not()). Strip these arguments
# before extracting "must be present" atoms, or such rules get wrongly
# dropped whenever their argument happens to reference a class that's
# unused elsewhere -- confirmed while building this: jw.org's own
# ".blockTxt:not(.dc-ttClassStyle--unset)" (a real, used, styled class)
# was wrongly dropped this way before this exclusion was added.
NEGATION_ARG_RE = re.compile(r':(?:not|is|where)\([^()]*\)', re.IGNORECASE)

XHTML_SUFFIXES = ('.xhtml', '.html', '.htm')


def _split_top_level_commas(tokens):
    """Split a component-value token list on top-level commas.

    tinycss2's parser already builds a tree -- a function like ":not(.foo)"
    is a single FunctionBlock token whose contents live in its own
    .arguments, never spilled into this flat list -- so any comma token we
    see here is already a genuine top-level selector-group separator.
    No paren/depth tracking is needed, and adding a naive one is actively
    wrong: a FunctionBlock's .type is 'function', which looks like "an
    opener" if you count it as +1 depth, but no matching literal ')' token
    ever arrives in this list to bring it back down (it's already been
    consumed into that one token) -- so a naive depth counter gets pegged
    at 1 forever after the first :not()/:is(), and every comma after that
    is silently treated as "nested" and never split on. (Found by testing
    against a real jw.org stylesheet: a 3-alternative selector group
    collapsed into 1, making an otherwise-matchable rule look unmatchable.)
    """
    groups = []
    current = []
    for tok in tokens:
        if tok.type == 'literal' and tok.value == ',':
            groups.append(current)
            current = []
            continue
        current.append(tok)
    groups.append(current)
    return groups


def _required_atoms(selector_group_tokens):
    """Class/id atoms that MUST be present somewhere in the document for
    this selector group to ever match -- excludes atoms that only appear
    inside a :not()/:is()/:where() argument (see NEGATION_ARG_RE above)."""
    text = tinycss2.serialize(selector_group_tokens)
    stripped = NEGATION_ARG_RE.sub(' ', text)
    return set(CLASS_OR_ID_RE.findall(stripped))


def _group_is_possible(selector_group_tokens, used_classes, used_ids):
    atoms = _required_atoms(selector_group_tokens)
    if not atoms:
        return True  # pure element/pseudo selector (e.g. "body", "p") -- keep
    for atom in atoms:
        kind, name = atom[0], atom[1:]
        if kind == '.' and name not in used_classes:
            return False
        if kind == '#' and name not in used_ids:
            return False
    return True


def _prune_rule_list(rule_list, used_classes, used_ids, stats):
    kept = []
    for rule in rule_list:
        if rule.type == 'qualified-rule':
            groups = _split_top_level_commas(rule.prelude)
            if any(_group_is_possible(g, used_classes, used_ids) for g in groups):
                kept.append(rule)
                stats['kept'] += 1
            else:
                stats['dropped'] += 1
        elif rule.type == 'at-rule' and rule.content is not None \
                and rule.at_keyword.lower() in ('media', 'supports'):
            nested = tinycss2.parse_rule_list(
                rule.content, skip_comments=True, skip_whitespace=True)
            rule.content = _prune_rule_list(nested, used_classes, used_ids, stats)
            kept.append(rule)
        else:
            # @font-face, @import, @keyframes, etc. -- always kept. (A
            # follow-up improvement would drop @font-face + its font file
            # when nothing surviving references that font-family anymore;
            # not done here, this pass only touches the stylesheet.)
            kept.append(rule)
    return kept


def _collect_used_tokens(zin, xhtml_names):
    used_classes, used_ids = set(), set()
    for n in xhtml_names:
        content = zin.read(n).decode('utf-8', 'replace')
        for m in re.findall(r'class="([^"]*)"', content):
            used_classes.update(m.split())
        used_ids.update(re.findall(r'id="([^"]*)"', content))
    return used_classes, used_ids


def prune_unused_css(in_path: str, out_path: str, log_fn=None) -> dict:
    """Rewrite the EPUB at `in_path` with every stylesheet's dead rules
    stripped, writing the result to `out_path`. Returns a summary dict."""
    def log(tag, message):
        if log_fn is not None:
            try:
                log_fn(tag, message)
            except Exception:
                pass

    zin = zipfile.ZipFile(in_path, 'r')
    try:
        names = zin.namelist()
        xhtml_names = [n for n in names if n.lower().endswith(XHTML_SUFFIXES)]
        css_names = [n for n in names if n.lower().endswith('.css')]
        used_classes, used_ids = _collect_used_tokens(zin, xhtml_names)
        log('INFO', 'Scanned %d document(s): %d used classes, %d used ids' % (
            len(xhtml_names), len(used_classes), len(used_ids)))

        summary = {'css_files': 0, 'rules_kept': 0, 'rules_dropped': 0,
                   'bytes_before': 0, 'bytes_after': 0}

        pruned_css = {}
        for css_name in css_names:
            css_text = zin.read(css_name).decode('utf-8', 'replace')
            rules = tinycss2.parse_stylesheet(
                css_text, skip_comments=True, skip_whitespace=True)
            stats = {'kept': 0, 'dropped': 0}
            kept_rules = _prune_rule_list(rules, used_classes, used_ids, stats)
            out_text = tinycss2.serialize(kept_rules)
            out_bytes = out_text.encode('utf-8')
            pruned_css[css_name] = out_bytes

            summary['css_files'] += 1
            summary['rules_kept'] += stats['kept']
            summary['rules_dropped'] += stats['dropped']
            summary['bytes_before'] += len(css_text.encode('utf-8'))
            summary['bytes_after'] += len(out_bytes)
            log('CSS', '%s: %d rules kept, %d dropped (%d -> %d bytes)' % (
                css_name, stats['kept'], stats['dropped'],
                len(css_text.encode('utf-8')), len(out_bytes)))

        with zipfile.ZipFile(out_path, 'w') as zout:
            if 'mimetype' in names:
                zout.writestr('mimetype', zin.read('mimetype'),
                              compress_type=zipfile.ZIP_STORED)
            for n in names:
                if n == 'mimetype':
                    continue
                info = zin.getinfo(n)
                if info.is_dir():
                    continue
                data = pruned_css.get(n)
                if data is None:
                    data = zin.read(n)
                zout.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
    finally:
        zin.close()

    saved = summary['bytes_before'] - summary['bytes_after']
    pct = (saved / summary['bytes_before'] * 100.0) if summary['bytes_before'] else 0.0
    log('DONE', 'Pruned %d rule(s) across %d stylesheet(s): %d -> %d bytes (-%.0f%%)' % (
        summary['rules_dropped'], summary['css_files'],
        summary['bytes_before'], summary['bytes_after'], pct))
    return summary
