// ============================================================
//  AKAV Crew Onboarding + Job Import  →  Google Sheet + Drive
//  Paste into the Apps Script editor attached to your Sheet.
//  Deploy: Execute as Me · Anyone (even anonymous)
//
//  Routes:
//    POST {no type}            → handleOnboarding (website form, unchanged)
//    POST {type:'jobImport'}   → handleJobImport  (pipeline/akav_import uploader)
//    GET  ?action=roster&token → roster JSON for local preview matching
//    GET  (anything else)      → live check
//
//  Setup: Project Settings → Script Properties → IMPORT_TOKEN = <shared secret>
//  (import + roster routes refuse to run until it is set)
// ============================================================

var DRIVE_FOLDER_NAME = 'AKAV Onboarding Uploads';

var MASTER_TAB      = 'Submissions';
var INCOMPLETE_TAB  = 'Incomplete Submissions';
var PROSPECTS_TAB   = 'Prospects';           // unreviewed new-email intakes
var DECLINED_TAB    = 'Declined Prospects';  // held back, recoverable
var WORKRECORDS_TAB = 'WorkRecords';
var SHOWS_TAB       = 'Shows';
var IMPORTLOG_TAB   = 'ImportLog';
var NOTES_HEADER    = 'Notes';
var GRADE_HEADER    = 'Rolly Grade';   // ad-hoc lead feedback from the
                                       // rolodex, vs per-show grades

// Per-show column group. Header = '<Show Label> — <field>'
var SHOW_FIELDS = ['Position(s)', 'Days', 'Rate', 'Grade'];

// Column definitions — order = sheet column order
var COLUMNS = [
  { key: 'createdAt',    header: 'Date Created',      type: 'date'   },
  { key: 'photo',        header: 'Photo',              type: 'photo'  },
  { key: 'name',         header: 'Name',               type: 'text'   },
  { key: 'email',        header: 'Email',              type: 'text'   },
  { key: 'phone',        header: 'Phone',              type: 'phone'  },
  { key: 'city',         header: 'City',               type: 'text'   },
  { key: 'w9',           header: 'W-9',                type: 'status' },
  { key: 'banking',      header: 'Banking',            type: 'status' },
  { key: 'address',      header: 'Address',            type: 'text'   },
  { key: 'workedBefore', header: 'Worked Before',      type: 'bool'   },
  { key: 'willTravel',   header: 'Will Travel',        type: 'bool'   },
  { key: 'travel',       header: 'Travel Radius',      type: 'text'   },
  { key: 'referredBy',   header: 'Referred By',        type: 'text'   },
  { key: 'pronouns',     header: 'Pronouns',           type: 'text'   },
  { key: 'linkedin',     header: 'LinkedIn',           type: 'text'   },
  { key: 'ecName',       header: 'Emergency Contact',  type: 'text'   },
  { key: 'ecPhone',      header: 'Emergency Phone',    type: 'phone'  },
  { key: 'ecRel',        header: 'Relation',           type: 'text'   },
  { key: 'resume',       header: 'Resume',             type: 'link'   },
  { key: 'rateSheet',    header: 'Rate Sheet',         type: 'link'   },
  { key: 'id',           header: 'ID',                 type: 'text'   },
];

// ── Helpers ────────────────────────────────────────────────

function getOrCreateFolder() {
  var folders = DriveApp.getFoldersByName(DRIVE_FOLDER_NAME);
  return folders.hasNext() ? folders.next() : DriveApp.createFolder(DRIVE_FOLDER_NAME);
}

function saveFileToDrive(dataUrl, filename) {
  if (!dataUrl) return '';
  try {
    var match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
    if (!match) return '';
    var bytes  = Utilities.base64Decode(match[2]);
    var blob   = Utilities.newBlob(bytes, match[1], filename);
    var file   = getOrCreateFolder().createFile(blob);
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    return file.getUrl();
  } catch (err) {
    return '';
  }
}

function getExt(dataUrl) {
  if (!dataUrl) return '';
  var m = dataUrl.match(/^data:([^;]+);/);
  if (!m) return '';
  var map = {
    'application/pdf': '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'image/jpeg': '.jpg', 'image/png': '.png',
    'image/gif': '.gif',  'image/webp': '.webp', 'image/heic': '.heic'
  };
  return map[m[1]] || '';
}

function isComplete(data) {
  return data.w9 === 'complete' && data.banking === 'complete';
}

function formatValue(col, data) {
  var v = data[col.key];
  if (v === undefined || v === null) v = '';

  switch (col.type) {
    case 'date':
      try {
        var d = new Date(v);
        return (d.getMonth()+1) + '/' + d.getDate() + '/' + d.getFullYear();
      } catch(e) { return String(v); }

    case 'bool':
      return (v === true || v === 'yes' || v === 'true') ? '✓' : '';

    case 'status':
      return v === 'complete' ? '✓' : '';

    case 'phone':
      return String(v);  // kept as string; column format set to text below

    case 'link':
    case 'photo':
      return '';  // handled post-append via setFormula

    default:
      return v === '' ? '' : String(v);
  }
}

// Sets up header row style + column widths (called once per sheet)
function applySheetFormatting(sheet) {
  var numCols = COLUMNS.length;
  var headerRange = sheet.getRange(1, 1, 1, numCols);
  headerRange.setFontWeight('bold');
  headerRange.setBackground('#222244');
  headerRange.setFontColor('#ffffff');
  headerRange.setHorizontalAlignment('center');
  sheet.setFrozenRows(1);

  var widths = {
    'Date Created': 100, 'Photo': 80,  'Name': 160, 'Email': 210, 'Phone': 120,
    'City': 100, 'W-9': 70, 'Banking': 80, 'Address': 200, 'Pronouns': 90,
    'Worked Before': 110, 'Will Travel': 90, 'Travel Radius': 120,
    'Referred By': 130, 'LinkedIn': 180,
    'Emergency Contact': 150, 'Emergency Phone': 130, 'Relation': 100,
    'Resume': 90, 'Rate Sheet': 100, 'ID': 120
  };
  for (var i = 0; i < COLUMNS.length; i++) {
    sheet.setColumnWidth(i + 1, widths[COLUMNS[i].header] || 120);
  }

  // Center-align bool/status/photo columns; left-align everything else
  for (var j = 0; j < COLUMNS.length; j++) {
    var t = COLUMNS[j].type;
    if (t === 'bool' || t === 'status' || t === 'photo') {
      sheet.getRange(2, j + 1, Math.max(sheet.getMaxRows() - 1, 1), 1)
           .setHorizontalAlignment('center');
    }
  }
}

// ── Routing ────────────────────────────────────────────────

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonOut({ ok: false, error: 'bad JSON: ' + String(err) });
  }
  if (data && data.type === 'jobImport') return handleJobImport(data);
  if (data && data.type === 'contactImport') return handleContactImport(data);
  if (data && data.type === 'approveProspect') return handleApproveProspect(data);
  if (data && data.type === 'declineProspect') return handleDeclineProspect(data);
  // POST variants of the admin reads — keeps the token out of URLs,
  // browser history, and proxy logs.
  if (data && data.type === 'people') return handlePeople(data.token);
  if (data && data.type === 'prospects') return handleProspects(data.token);
  return handleOnboarding(data);
}

function doGet(e) {
  if (e && e.parameter && e.parameter.action === 'roster') {
    return handleRoster(e.parameter.token);
  }
  if (e && e.parameter && e.parameter.action === 'people') {
    return handlePeople(e.parameter.token);
  }
  if (e && e.parameter && e.parameter.action === 'prospects') {
    return handleProspects(e.parameter.token);
  }
  return jsonOut({ ok: true, msg: 'AKAV endpoint live' });
}

// Full people dump for the admin portal: every column of a tab, one
// object per person keyed by header text. Token-gated like roster.
function dumpTabAsPeople(tabName, token) {
  if (!tokenOk(token)) return jsonOut({ ok: false, error: 'unauthorized' });
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(tabName);
  if (!sheet || sheet.getLastRow() < 2) {
    return jsonOut({ ok: true, headers: [], people: [] });
  }
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
                     .map(function(h) { return String(h || '').trim(); });
  var display = sheet.getRange(2, 1, sheet.getLastRow() - 1, lastCol)
                     .getDisplayValues();
  var people = [];
  for (var r = 0; r < display.length; r++) {
    var obj = { _row: r + 2 };
    var any = false;
    for (var c = 0; c < headers.length; c++) {
      if (!headers[c]) continue;
      obj[headers[c]] = display[r][c];
      if (display[r][c]) any = true;
    }
    if (any) people.push(obj);
  }
  return jsonOut({ ok: true, headers: headers.filter(String),
                   people: people });
}

function handlePeople(token) {
  return dumpTabAsPeople(MASTER_TAB, token);
}

function handleProspects(token) {
  return dumpTabAsPeople(PROSPECTS_TAB, token);
}

// ── Prospect review (admin portal) ─────────────────────────
//  POST { type:'approveProspect', token, row, email }
//  POST { type:'declineProspect', token, row, email }
//  `email` is a guard: row numbers shift as rows are approved/declined, so
//  the row must still hold the expected email (or name when email empty).

function _prospectRow(src, data) {
  var row = Number(data.row || 0);
  if (row < 2 || row > src.getLastRow()) return null;
  var idx = buildHeaderIndex(src);
  var vals = src.getRange(row, 1, 1, src.getLastColumn()).getValues()[0];
  var email = idx['Email'] ? normEmail(vals[idx['Email'] - 1]) : '';
  var name = idx['Name'] ? normName(vals[idx['Name'] - 1]) : '';
  var expected = normEmail(data.email);
  if (expected && email !== expected &&
      normName(data.email) !== name) {
    return { stale: true };
  }
  return { row: row, vals: vals, idx: idx };
}

function handleApproveProspect(data) {
  if (!tokenOk(data.token)) return jsonOut({ ok: false, error: 'unauthorized' });
  var lock = LockService.getScriptLock();
  try { lock.waitLock(30000); } catch (e) {
    return jsonOut({ ok: false, error: 'lock timeout' });
  }
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var src = ss.getSheetByName(PROSPECTS_TAB);
    var dst = getMasterSheet(ss);
    if (!src) return jsonOut({ ok: false, error: 'no prospects tab' });
    var p = _prospectRow(src, data);
    if (!p) return jsonOut({ ok: false, error: 'bad row' });
    if (p.stale) return jsonOut({ ok: false, error: 'row moved — refresh prospects' });

    var core = p.vals.slice(0, COLUMNS.length);
    // Re-check identity: an import may have added them to the master
    // between submission and approval.
    var existing = null;
    if (dst.getLastRow() >= 2) {
      var hIdx = buildHeaderIndex(src);
      existing = findPersonRow(buildPersonIndex(dst), {
        email: hIdx['Email'] ? p.vals[hIdx['Email'] - 1] : '',
        phoneDigits: hIdx['Phone'] ? p.vals[hIdx['Phone'] - 1] : '',
        name: hIdx['Name'] ? p.vals[hIdx['Name'] - 1] : '' });
    }
    var target;
    if (existing) {
      dst.getRange(existing, 1, 1, COLUMNS.length).setValues([core]);
      target = existing;
    } else {
      target = dst.getLastRow() + 1;
      dst.getRange(target, 1, 1, COLUMNS.length).setValues([core]);
    }
    // Positions travels by header, not by index
    if (p.idx['Positions']) {
      var pos = p.vals[p.idx['Positions'] - 1];
      if (pos) {
        dst.getRange(target, ensurePositionsColumn(dst)).setValue(pos);
      }
    }
    src.deleteRow(p.row);
    return jsonOut({ ok: true, row: target, merged: !!existing });
  } catch (err) {
    return jsonOut({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (e2) {}
  }
}

function handleDeclineProspect(data) {
  if (!tokenOk(data.token)) return jsonOut({ ok: false, error: 'unauthorized' });
  var lock = LockService.getScriptLock();
  try { lock.waitLock(30000); } catch (e) {
    return jsonOut({ ok: false, error: 'lock timeout' });
  }
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var src = ss.getSheetByName(PROSPECTS_TAB);
    if (!src) return jsonOut({ ok: false, error: 'no prospects tab' });
    var p = _prospectRow(src, data);
    if (!p) return jsonOut({ ok: false, error: 'bad row' });
    if (p.stale) return jsonOut({ ok: false, error: 'row moved — refresh prospects' });

    var dst = ss.getSheetByName(DECLINED_TAB);
    if (!dst) {
      dst = ss.insertSheet(DECLINED_TAB);
      dst.appendRow(COLUMNS.map(function(c) { return c.header; }));
      applySheetFormatting(dst);
      dst.hideSheet();
    }
    dst.getRange(dst.getLastRow() + 1, 1, 1, p.vals.length)
       .setValues([p.vals]);
    src.deleteRow(p.row);
    return jsonOut({ ok: true });
  } catch (err) {
    return jsonOut({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (e2) {}
  }
}

function jsonOut(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function importToken() {
  return PropertiesService.getScriptProperties().getProperty('IMPORT_TOKEN') || '';
}

function tokenOk(token) {
  var expected = importToken();
  return !!expected && token === expected;
}

// ── Onboarding handler (website form — body unchanged) ─────

function handleOnboarding(data) {
  var lock = LockService.getScriptLock();
  lock.tryLock(30000);
  try {
    var ss   = SpreadsheetApp.getActiveSpreadsheet();
    var safeName = String(data.name || 'unknown').replace(/[^a-zA-Z0-9_\- ]/g, '').trim();

    // Upload files to Drive; replace fields with Drive URLs
    if (data.resumeData) {
      data.resume = saveFileToDrive(
        data.resumeData, safeName + '_resume_' + (data.id || '') + getExt(data.resumeData));
    }
    if (data.rateSheetData) {
      data.rateSheet = saveFileToDrive(
        data.rateSheetData, safeName + '_ratesheet_' + (data.id || '') + getExt(data.rateSheetData));
    }
    if (data.photoData) {
      data.photo = saveFileToDrive(
        data.photoData, safeName + '_photo_' + (data.id || '') + getExt(data.photoData));
    }

    // Route to correct tab
    var tabName = isComplete(data) ? MASTER_TAB : INCOMPLETE_TAB;
    var sheet   = ss.getSheetByName(tabName) || ss.insertSheet(tabName);

    // First-time setup: write header row + formatting
    var isNewSheet = (sheet.getLastRow() === 0);
    if (isNewSheet) {
      sheet.appendRow(COLUMNS.map(function(c) { return c.header; }));
      applySheetFormatting(sheet);
    }

    // Build plain-value row (links/photos handled separately)
    var row = COLUMNS.map(function(col) { return formatValue(col, data); });

    // Upsert: a job import may already have created this person's row in
    // the master tab. If they match by email/phone/name, fill their
    // onboarding columns in place — never append a duplicate. Only the
    // first COLUMNS.length columns are written; Notes and per-show
    // columns (22+) are untouched. Guarded by a legacy-layout check so a
    // manually rearranged sheet degrades to the old append behavior.
    var newRow = null;
    var master = ss.getSheetByName(MASTER_TAB);
    if (master && master.getLastRow() >= 2) {
      var hIdx = buildHeaderIndex(master);
      var legacyLayout = (hIdx['Name'] === 3 && hIdx['Email'] === 4 &&
                          hIdx['Phone'] === 5);
      if (legacyLayout) {
        var existingRow = findPersonRow(buildPersonIndex(master), {
          email: data.email, phoneDigits: data.phone, name: data.name });
        if (existingRow) {
          sheet = master;
          newRow = existingRow;
          sheet.getRange(newRow, 1, 1, COLUMNS.length).setValues([row]);
        }
      }
    }
    if (newRow === null) {
      // No identity match → someone we've never seen. Complete submissions
      // wait in Prospects for review instead of joining the master roster
      // (Henry's rule, 2026-08-09). Incomplete ones keep going to
      // INCOMPLETE_TAB as before.
      if (tabName === MASTER_TAB) {
        sheet = ss.getSheetByName(PROSPECTS_TAB) || ss.insertSheet(PROSPECTS_TAB);
        if (sheet.getLastRow() === 0) {
          sheet.appendRow(COLUMNS.map(function(c) { return c.header; }));
          applySheetFormatting(sheet);
        }
      }
      newRow = sheet.getLastRow() + 1;
      sheet.appendRow(row);
    }

    // Positions the person says they work (intake form array). Header-
    // driven on purpose: adding it to COLUMNS would make the 1–21 upsert
    // overwrite the Notes column.
    if (data.positions && data.positions.length) {
      var posCol = ensurePositionsColumn(sheet);
      var posVal = (Array.isArray(data.positions)
                    ? data.positions.join(', ') : String(data.positions));
      sheet.getRange(newRow, posCol).setValue(posVal);
    }

    // Post-append: phone columns → text format (prevents right-justification)
    for (var i = 0; i < COLUMNS.length; i++) {
      if (COLUMNS[i].type === 'phone') {
        var cell = sheet.getRange(newRow, i + 1);
        cell.setNumberFormat('@');
        cell.setValue(String(data[COLUMNS[i].key] || ''));
      }
    }

    // Post-append: file link columns → HYPERLINK formula
    for (var j = 0; j < COLUMNS.length; j++) {
      var col = COLUMNS[j];
      if (col.type === 'link' || col.type === 'photo') {
        var url = data[col.key] || '';
        if (url && url.indexOf('http') === 0) {
          var label = col.type === 'photo' ? 'Photo' : col.header;
          // Escape any double-quotes in the URL (shouldn't occur but safe)
          var safeUrl = url.replace(/"/g, '""');
          sheet.getRange(newRow, j + 1)
               .setFormula('=HYPERLINK("' + safeUrl + '","' + label + '")');
        }
      }
    }

    return jsonOut({ ok: true });

  } catch (err) {
    return jsonOut({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// ── Roster (GET, token-gated — feeds local preview matching) ─

function handleRoster(token) {
  if (!tokenOk(token)) return jsonOut({ ok: false, error: 'unauthorized' });
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MASTER_TAB);
  if (!sheet || sheet.getLastRow() < 2) return jsonOut({ ok: true, roster: [] });

  var idx    = buildHeaderIndex(sheet);
  var nameC  = idx['Name'], emailC = idx['Email'], phoneC = idx['Phone'];
  var values = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var roster = [];
  for (var r = 0; r < values.length; r++) {
    var row = values[r];
    var name = nameC ? String(row[nameC - 1] || '') : '';
    var email = emailC ? String(row[emailC - 1] || '') : '';
    var phone = phoneC ? String(row[phoneC - 1] || '') : '';
    if (!name && !email && !phone) continue;
    roster.push({
      row: r + 2,
      name: name,
      email: normEmail(email),
      phoneDigits: normPhone(phone)
    });
  }
  return jsonOut({ ok: true, roster: roster });
}

// ══════════════════════════════════════════════════════════
//  JOB IMPORT MODULE
//  Payload (one chunk):
//  { type:'jobImport', token, batchId, chunkIndex, chunkCount,
//    finalize:bool, sourceFile,
//    show: { showId, showLabel, venue, client, po, pm, firstDate, lastDate },
//    records: [ { recordHash, personKey, date, position, callStart, callEnd,
//                 rate, area, otNote, name, email, phoneDigits, sourceSheet } ],
//    people:  [ { personKey, name, email, phoneDigits, grade, notes, total } ]
//               (sent on the finalize chunk only)                          }
// ══════════════════════════════════════════════════════════

// ── Identity normalization ─────────────────────────────────
// MUST stay byte-for-byte consistent with pipeline/akav_import/normalize.py

function normEmail(s) {
  return String(s || '').trim().toLowerCase();
}

function normPhone(s) {
  var d = String(s || '').replace(/\D/g, '');
  if (d.length === 11 && d.charAt(0) === '1') d = d.substring(1);
  return d;
}

function normName(s) {
  var t = String(s || '').toLowerCase();
  try { t = t.normalize('NFD').replace(/[\u0300-\u036f]/g, ''); } catch (e) {}
  t = t.replace(/[^a-z0-9 ]/g, ' ');
  t = t.replace(/\s+/g, ' ').trim();
  return t;
}

// ── Master-sheet helpers ───────────────────────────────────

// Row-1 header text (trimmed) → 1-based column index
function buildHeaderIndex(sheet) {
  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return {};
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var idx = {};
  for (var c = 0; c < headers.length; c++) {
    var h = String(headers[c] || '').trim();
    if (h && !(h in idx)) idx[h] = c + 1;
  }
  return idx;
}

function getMasterSheet(ss) {
  var sheet = ss.getSheetByName(MASTER_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(MASTER_TAB);
    sheet.appendRow(COLUMNS.map(function(c) { return c.header; }));
    applySheetFormatting(sheet);
  }
  return sheet;
}

// Ensure the persistent Notes column exists right after the onboarding block.
function ensureNotesColumn(sheet) {
  var idx = buildHeaderIndex(sheet);
  if (idx[NOTES_HEADER]) return idx[NOTES_HEADER];
  var after = COLUMNS.length;                       // col 21
  if (sheet.getLastColumn() < after) after = sheet.getLastColumn();
  sheet.insertColumnAfter(after);
  var col = after + 1;
  var cell = sheet.getRange(1, col);
  cell.setValue(NOTES_HEADER);
  cell.setFontWeight('bold').setBackground('#222244')
      .setFontColor('#ffffff').setHorizontalAlignment('center');
  sheet.setColumnWidth(col, 260);
  return col;
}

// Ensure the Positions column (self-declared roles from the intake form).
// Sits right after the 21 onboarding columns, before Notes. Header-driven —
// deliberately NOT in COLUMNS, so the 1–21 upsert can't clobber it.
function ensurePositionsColumn(sheet) {
  var idx = buildHeaderIndex(sheet);
  if (idx['Positions']) return idx['Positions'];
  var after = Math.min(COLUMNS.length, sheet.getLastColumn());
  sheet.insertColumnAfter(after);
  var col = after + 1;
  var cell = sheet.getRange(1, col);
  cell.setValue('Positions');
  cell.setFontWeight('bold').setBackground('#222244')
      .setFontColor('#ffffff').setHorizontalAlignment('center');
  sheet.setColumnWidth(col, 160);
  return col;
}

// Ensure the Rolly Lists column (which rolodex tabs a person appears on —
// 'LA Short list' drives the portal's shortlist stars).
function ensureRollyListsColumn(sheet) {
  var idx = buildHeaderIndex(sheet);
  if (idx['Rolly Lists']) return idx['Rolly Lists'];
  var col = sheet.getLastColumn() + 1;
  var anchor = idx[GRADE_HEADER] || idx[NOTES_HEADER];
  if (anchor) {
    sheet.insertColumnAfter(anchor);
    col = anchor + 1;
  }
  var cell = sheet.getRange(1, col);
  cell.setValue('Rolly Lists');
  cell.setFontWeight('bold').setBackground('#222244')
      .setFontColor('#ffffff').setHorizontalAlignment('center');
  sheet.setColumnWidth(col, 180);
  return col;
}

// Ensure the Rolly Grade column sits right after Notes.
function ensureGradeColumn(sheet) {
  var idx = buildHeaderIndex(sheet);
  if (idx[GRADE_HEADER]) return idx[GRADE_HEADER];
  // Migration: earlier versions named this column 'Grade' / 'Overall
  // Grade' — rename in place rather than inserting a duplicate.
  var legacy = idx['Grade'] || idx['Overall Grade'];
  if (legacy) {
    sheet.getRange(1, legacy).setValue(GRADE_HEADER);
    return legacy;
  }
  var notesCol = ensureNotesColumn(sheet);
  sheet.insertColumnAfter(notesCol);
  var col = notesCol + 1;
  var cell = sheet.getRange(1, col);
  cell.setValue(GRADE_HEADER);
  cell.setFontWeight('bold').setBackground('#222244')
      .setFontColor('#ffffff').setHorizontalAlignment('center');
  sheet.setColumnWidth(col, 70);
  return col;
}

// One getValues() pass over the master → identity maps (first match wins)
function buildPersonIndex(sheet) {
  var idx = buildHeaderIndex(sheet);
  var out = { emailMap: {}, phoneMap: {}, nameMap: {}, headerIndex: idx };
  if (sheet.getLastRow() < 2) return out;
  var nameC = idx['Name'], emailC = idx['Email'], phoneC = idx['Phone'];
  var values = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  for (var r = 0; r < values.length; r++) {
    var rowNum = r + 2;
    var email = emailC ? normEmail(values[r][emailC - 1]) : '';
    var phone = phoneC ? normPhone(values[r][phoneC - 1]) : '';
    var name  = nameC  ? normName(values[r][nameC - 1])   : '';
    if (email && !(email in out.emailMap)) out.emailMap[email] = rowNum;
    if (phone && !(phone in out.phoneMap)) out.phoneMap[phone] = rowNum;
    if (name  && !(name  in out.nameMap))  out.nameMap[name]   = rowNum;
  }
  return out;
}

// email → phone digits → normalized name; null when no match
function findPersonRow(personIndex, person) {
  var email = normEmail(person.email);
  if (email && personIndex.emailMap[email]) return personIndex.emailMap[email];
  var phone = normPhone(person.phoneDigits || person.phone);
  if (phone && personIndex.phoneMap[phone]) return personIndex.phoneMap[phone];
  var name = normName(person.name);
  if (name && personIndex.nameMap[name]) return personIndex.nameMap[name];
  return null;
}

// Minimal row for an import-created person (they may onboard later)
function createPersonRow(sheet, person) {
  var idx = buildHeaderIndex(sheet);
  var rowNum = sheet.getLastRow() + 1;
  var d = new Date();
  var vals = {};
  vals['Date Created'] = (d.getMonth()+1) + '/' + d.getDate() + '/' + d.getFullYear();
  vals['Name']  = person.name || '';
  vals['Email'] = person.email || '';
  if (person.city) vals['City'] = person.city;
  vals['ID']    = 'imp_' + (person.personKey || '').replace(/[^a-zA-Z0-9]/g, '_').substring(0, 40);
  for (var header in vals) {
    if (idx[header]) sheet.getRange(rowNum, idx[header]).setValue(vals[header]);
  }
  if (idx['Phone']) {
    var cell = sheet.getRange(rowNum, idx['Phone']);
    cell.setNumberFormat('@');
    cell.setValue(String(person.phoneDigits || ''));
  }
  return rowNum;
}

// ── Shows registry ─────────────────────────────────────────

var SHOWS_HEADERS = ['showId', 'showLabel', 'venue', 'client', 'po', 'pm',
                     'firstDate', 'lastDate', 'colStart', 'importedAt', 'lastBatchId'];

function getShowsSheet(ss) {
  var sheet = ss.getSheetByName(SHOWS_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(SHOWS_TAB);
    sheet.appendRow(SHOWS_HEADERS);
    sheet.getRange(1, 1, 1, SHOWS_HEADERS.length).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function upsertShow(ss, show, batchId) {
  var sheet = ss.getSheetByName(SHOWS_TAB) || getShowsSheet(ss);
  var lastRow = sheet.getLastRow();
  var rowNum = 0;
  if (lastRow >= 2) {
    var ids = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    for (var r = 0; r < ids.length; r++) {
      if (String(ids[r][0]) === show.showId) { rowNum = r + 2; break; }
    }
  }
  var existingColStart = '';
  if (rowNum) {
    existingColStart = sheet.getRange(rowNum, 9).getValue();
  } else {
    rowNum = lastRow + 1;
  }
  sheet.getRange(rowNum, 1, 1, SHOWS_HEADERS.length).setValues([[
    show.showId, show.showLabel || '', show.venue || '', show.client || '',
    show.po || '', show.pm || '', show.firstDate || '', show.lastDate || '',
    existingColStart, new Date().toISOString(), batchId || ''
  ]]);
  return rowNum;
}

function setShowColStart(ss, showId, colStart) {
  var sheet = getShowsSheet(ss);
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  var ids = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  for (var r = 0; r < ids.length; r++) {
    if (String(ids[r][0]) === showId) {
      sheet.getRange(r + 2, 9).setValue(colStart);
      return;
    }
  }
}

// Find the show's summary columns on the master, appending any missing
// ones. Returns {field: colIndex} — columns are located BY HEADER, so a
// change to SHOW_FIELDS (or a manually deleted/moved column) can never
// misalign writes into a neighboring column.
function ensureShowColumnGroup(ss, sheet, showLabel, showId) {
  var idx = buildHeaderIndex(sheet);
  var cols = {};
  var missing = [];
  SHOW_FIELDS.forEach(function(f) {
    var h = showLabel + ' — ' + f;
    if (idx[h]) cols[f] = idx[h];
    else missing.push(f);
  });
  if (missing.length) {
    var startCol = sheet.getLastColumn() + 1;
    var headers = missing.map(function(f) { return showLabel + ' — ' + f; });
    sheet.getRange(1, startCol, 1, missing.length).setValues([headers]);
    sheet.getRange(1, startCol, 1, missing.length)
         .setFontWeight('bold').setBackground('#2e4a22')
         .setFontColor('#ffffff').setHorizontalAlignment('center');
    for (var i = 0; i < missing.length; i++) {
      sheet.setColumnWidth(startCol + i, missing[i] === 'Position(s)' ? 180 : 90);
      cols[missing[i]] = startCol + i;
    }
  }
  setShowColStart(ss, showId, cols[SHOW_FIELDS[0]]);
  return cols;
}

// ── WorkRecords (hidden, append-only source of truth) ──────

var WR_HEADERS = ['importedAt', 'batchId', 'showId', 'showLabel', 'date',
                  'personKey', 'name', 'email', 'phoneDigits', 'position',
                  'callStart', 'callEnd', 'rate', 'area', 'otNote',
                  'sourceSheet', 'sourceFile', 'recordHash'];

function getWorkRecordsSheet(ss) {
  var sheet = ss.getSheetByName(WORKRECORDS_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(WORKRECORDS_TAB);
    sheet.appendRow(WR_HEADERS);
    sheet.getRange(1, 1, 1, WR_HEADERS.length).setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.hideSheet();
  }
  return sheet;
}

function loadExistingHashes(sheet, showId) {
  var hashes = {};
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return hashes;
  var vals = sheet.getRange(2, 1, lastRow - 1, WR_HEADERS.length).getValues();
  var showC = WR_HEADERS.indexOf('showId');
  var hashC = WR_HEADERS.indexOf('recordHash');
  for (var r = 0; r < vals.length; r++) {
    if (String(vals[r][showC]) === showId) hashes[String(vals[r][hashC])] = true;
  }
  return hashes;
}

// Batched append; skips records whose recordHash already exists for the show.
function appendWorkRecords(sheet, records, existingHashes, show, batchId, sourceFile) {
  var now = new Date().toISOString();
  var rows = [];
  var skipped = 0;
  for (var i = 0; i < records.length; i++) {
    var rec = records[i];
    if (existingHashes[rec.recordHash]) { skipped++; continue; }
    existingHashes[rec.recordHash] = true;   // dedupe within the chunk too
    rows.push([
      now, batchId, show.showId, show.showLabel || '', rec.date || '',
      rec.personKey || '', rec.name || '', rec.email || '', rec.phoneDigits || '',
      rec.position || '', rec.callStart || '', rec.callEnd || '',
      (rec.rate === null || rec.rate === undefined) ? '' : rec.rate,
      rec.area || '', rec.otNote || '', rec.sourceSheet || '', sourceFile || '',
      rec.recordHash
    ]);
  }
  if (rows.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, WR_HEADERS.length)
         .setValues(rows);
  }
  return { appended: rows.length, skipped: skipped };
}

// ── Notes & grades ─────────────────────────────────────────

// Merge-only: never clobbers; skips if already present (substring check).
// label is optional — show imports tag notes with the show name, rolodex
// notes go in untagged.
function mergeNotes(existing, incoming, label) {
  var inc = String(incoming || '').trim();
  if (!inc) return existing;
  var ex = String(existing || '');
  if (ex.indexOf(inc) !== -1) return ex;
  var tagged = label ? '[' + label + '] ' + inc : inc;
  return ex ? ex + '\n' + tagged : tagged;
}

// ── Summary recompute (runs on the finalize chunk) ─────────

function recomputeShowSummary(ss, show, people) {
  var master = getMasterSheet(ss);
  ensureNotesColumn(master);
  var fcols = ensureShowColumnGroup(ss, master, show.showLabel, show.showId);
  var wr = getWorkRecordsSheet(ss);

  // Aggregate this show's WorkRecords by personKey
  var lastRow = wr.getLastRow();
  var agg = {};   // personKey → {dates:{}, positions:{}, rates:[], name,email,phone}
  if (lastRow >= 2) {
    var vals = wr.getRange(2, 1, lastRow - 1, WR_HEADERS.length).getValues();
    var C = {};
    for (var h = 0; h < WR_HEADERS.length; h++) C[WR_HEADERS[h]] = h;
    for (var r = 0; r < vals.length; r++) {
      if (String(vals[r][C.showId]) !== show.showId) continue;
      var key = String(vals[r][C.personKey]);
      if (!agg[key]) {
        agg[key] = { dates: {}, positions: {}, rates: [],
                     name: String(vals[r][C.name] || ''),
                     email: String(vals[r][C.email] || ''),
                     phoneDigits: String(vals[r][C.phoneDigits] || '') };
      }
      var a = agg[key];
      var dt = String(vals[r][C.date] || '');
      if (dt) a.dates[dt] = true;
      var pos = String(vals[r][C.position] || '').trim();
      if (pos) a.positions[pos] = (a.positions[pos] || 0) + 1;
      var rate = vals[r][C.rate];
      if (rate !== '' && rate !== null && !isNaN(Number(rate))) a.rates.push(Number(rate));
    }
  }

  // Person-level meta (grade, notes, Crew Status total) sent on the
  // finalize chunk. People with status data but no parsed day records
  // (partial workbooks) still get a row + total/grade/notes — add them
  // to the aggregate set with empty day data.
  var meta = {};
  (people || []).forEach(function(p) {
    meta[p.personKey] = p;
    if (!agg[p.personKey]) {
      agg[p.personKey] = { dates: {}, positions: {}, rates: [],
                           name: p.name || '', email: p.email || '',
                           phoneDigits: p.phoneDigits || '' };
    }
  });

  var personIndex = buildPersonIndex(master);
  var notesCol = buildHeaderIndex(master)[NOTES_HEADER];
  var created = 0, matched = 0;

  for (var key in agg) {
    var a = agg[key];
    var m = meta[key] || {};
    var person = { personKey: key, name: m.name || a.name,
                   email: m.email || a.email,
                   phoneDigits: m.phoneDigits || a.phoneDigits };
    var rowNum = findPersonRow(personIndex, person);
    if (rowNum) {
      matched++;
    } else {
      rowNum = createPersonRow(master, person);
      created++;
      // register the new row so a second personKey for the same human matches it
      var e2 = normEmail(person.email), p2 = normPhone(person.phoneDigits),
          n2 = normName(person.name);
      if (e2) personIndex.emailMap[e2] = rowNum;
      if (p2) personIndex.phoneMap[p2] = rowNum;
      if (n2) personIndex.nameMap[n2] = rowNum;
    }

    // Positions: most frequent first
    var posList = Object.keys(a.positions).sort(function(x, y) {
      return a.positions[y] - a.positions[x];
    });
    var days = Object.keys(a.dates).length;
    var rateStr = '';
    if (a.rates.length) {
      var mn = Math.min.apply(null, a.rates), mx = Math.max.apply(null, a.rates);
      rateStr = (mn === mx) ? String(mn) : mn + '–' + mx;
    }

    // Derived cells: always overwritten. Grade: write-if-provided, never blanked.
    var derived = [['Position(s)', posList.join(', ')], ['Days', days],
                   ['Rate', rateStr]];
    // Normal case: the three derived columns are contiguous — one write.
    if (fcols['Days'] === fcols['Position(s)'] + 1 &&
        fcols['Rate'] === fcols['Position(s)'] + 2) {
      master.getRange(rowNum, fcols['Position(s)'], 1, 3)
            .setValues([[derived[0][1], derived[1][1], derived[2][1]]]);
    } else {
      derived.forEach(function(d) {
        master.getRange(rowNum, fcols[d[0]]).setValue(d[1]);
      });
    }
    var dateKeys = Object.keys(a.dates).sort();
    if (dateKeys.length) {
      master.getRange(rowNum, fcols['Days'])
            .setNote(dateKeys[0] + ' → ' + dateKeys[dateKeys.length - 1]);
    }
    if (m.grade) master.getRange(rowNum, fcols['Grade']).setValue(m.grade);

    if (notesCol && m.notes) {
      var noteCell = master.getRange(rowNum, notesCol);
      noteCell.setValue(mergeNotes(noteCell.getValue(), m.notes, show.showLabel));
    }
  }
  return { matched: matched, created: created, people: Object.keys(agg).length };
}

// ── Import log ─────────────────────────────────────────────

var LOG_HEADERS = ['timestamp', 'batchId', 'chunkIndex', 'chunkCount', 'showId',
                   'received', 'appended', 'skippedDupes', 'finalize',
                   'peopleMatched', 'peopleCreated', 'error'];

function logImport(ss, entry) {
  var sheet = ss.getSheetByName(IMPORTLOG_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(IMPORTLOG_TAB);
    sheet.appendRow(LOG_HEADERS);
    sheet.getRange(1, 1, 1, LOG_HEADERS.length).setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.hideSheet();
  }
  sheet.appendRow(LOG_HEADERS.map(function(h) {
    return entry[h] === undefined ? '' : entry[h];
  }));
}

// ── Contact import (crew rolodex → City upsert) ────────────
//  Payload: { type:'contactImport', token, batchId, chunkIndex, chunkCount,
//             contacts: [{name, email, phoneDigits, city}] }
//  Matched people: City is written ONLY if currently blank (onboarding
//  data wins). Unknown people get a new minimal row with City set.

function handleContactImport(data) {
  if (!tokenOk(data.token)) return jsonOut({ ok: false, error: 'unauthorized' });

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(60000);
  } catch (err) {
    return jsonOut({ ok: false, error: 'lock timeout' });
  }

  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var master = getMasterSheet(ss);
    var notesCol = ensureNotesColumn(master);
    var gradeCol = ensureGradeColumn(master);
    var listsCol = ensureRollyListsColumn(master);
    var idx = buildHeaderIndex(master);
    var cityCol = idx['City'];
    var personIndex = buildPersonIndex(master);
    var matched = 0, created = 0, cityWritten = 0, cityKept = 0;

    (data.contacts || []).forEach(function(c) {
      var rowNum = findPersonRow(personIndex, c);
      if (rowNum) {
        matched++;
        if (c.city && cityCol) {
          var cell = master.getRange(rowNum, cityCol);
          if (!String(cell.getValue() || '').trim()) {
            cell.setValue(c.city);
            cityWritten++;
          } else {
            cityKept++;
          }
        }
      } else {
        rowNum = createPersonRow(master, c);
        created++;
        if (c.city) cityWritten++;
        var e2 = normEmail(c.email), p2 = normPhone(c.phoneDigits),
            n2 = normName(c.name);
        if (e2) personIndex.emailMap[e2] = rowNum;
        if (p2) personIndex.phoneMap[p2] = rowNum;
        if (n2) personIndex.nameMap[n2] = rowNum;
      }
      // Skills / comments from the rolodex → Notes (merge-only, untagged)
      if (c.notes && notesCol) {
        var noteCell = master.getRange(rowNum, notesCol);
        noteCell.setValue(mergeNotes(noteCell.getValue(), c.notes, ''));
      }
      // General grade → Grade column (write-if-provided, never blanked)
      if (c.grade && gradeCol) {
        master.getRange(rowNum, gradeCol).setValue(c.grade);
      }
      // Rolodex tab membership ('LA Short list' → portal shortlist).
      // Latest import wins — list membership is current-state, not history.
      if (c.lists && c.lists.length && listsCol) {
        master.getRange(rowNum, listsCol).setValue(c.lists.join(', '));
      }
    });

    logImport(ss, {
      timestamp: new Date().toISOString(), batchId: data.batchId,
      chunkIndex: data.chunkIndex, chunkCount: data.chunkCount,
      showId: 'contactImport', received: (data.contacts || []).length,
      appended: created, skippedDupes: cityKept, finalize: '',
      peopleMatched: matched, peopleCreated: created, error: ''
    });
    return jsonOut({ ok: true, batchId: data.batchId,
                     chunkIndex: data.chunkIndex, matched: matched,
                     created: created, cityWritten: cityWritten,
                     cityKept: cityKept });
  } catch (err) {
    return jsonOut({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// ── One-time reset: archive test tabs, start a clean database ──
//
//  The spreadsheet is full of test rows from building the form. Run
//  initFreshDatabase() ONCE from the Apps Script editor (select it in
//  the function dropdown, press Run). It:
//    1. renames every existing data tab to 'OLD <name> <M/D>' and hides it
//       (nothing is deleted — you can still look at the test data)
//    2. creates fresh, correctly formatted Submissions / Incomplete
//       Submissions (21 onboarding columns + Notes)
//  WorkRecords / Shows / ImportLog are created automatically by the first
//  import. When you're sure you don't need the old tabs:
//  run deleteArchivedTabs().

function initFreshDatabase() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var d = new Date();
  var stamp = (d.getMonth() + 1) + '/' + d.getDate();
  var targets = [MASTER_TAB, INCOMPLETE_TAB, WORKRECORDS_TAB,
                 SHOWS_TAB, IMPORTLOG_TAB, PROSPECTS_TAB, DECLINED_TAB];

  // 1. Rename existing data tabs to 'OLD <name> <date>' (don't hide yet —
  //    Sheets refuses to hide the last visible sheet)
  targets.forEach(function(name) {
    var sheet = ss.getSheetByName(name);
    if (!sheet) return;   // already archived (e.g. re-run after an error)
    var archived = 'OLD ' + name + ' ' + stamp;
    var n = 2;
    while (ss.getSheetByName(archived)) {
      archived = 'OLD ' + name + ' ' + stamp + ' (' + n + ')';
      n++;
    }
    sheet.setName(archived);
  });

  // 2. Fresh onboarding tabs with the proper 21-column layout
  [MASTER_TAB, INCOMPLETE_TAB].forEach(function(name) {
    if (ss.getSheetByName(name)) return;   // survived a partial earlier run
    var sheet = ss.insertSheet(name);
    sheet.appendRow(COLUMNS.map(function(c) { return c.header; }));
    applySheetFormatting(sheet);
  });
  ensureNotesColumn(ss.getSheetByName(MASTER_TAB));

  // 3. Now that fresh tabs exist and are visible, hide every archive
  ss.getSheets().forEach(function(sheet) {
    if (sheet.getName().indexOf('OLD ') === 0) {
      try { sheet.hideSheet(); } catch (e) {}
    }
  });

  ss.setActiveSheet(ss.getSheetByName(MASTER_TAB));
}

// One-time cleanup after the note-format change: strips '[Rolly] ' tags
// and 'grade X; ' fragments already written into Notes by earlier rolly
// imports (the grade now lives in the Grade column). Run from the editor.
function cleanupRollyNotes() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var master = ss.getSheetByName(MASTER_TAB);
  if (!master || master.getLastRow() < 2) return;
  var notesCol = buildHeaderIndex(master)[NOTES_HEADER];
  if (!notesCol) return;
  var rng = master.getRange(2, notesCol, master.getLastRow() - 1, 1);
  var vals = rng.getValues();
  var gradeFrag = /(^|\n|; )grade\s+[A-DFXa-dfx][+-]?(\s*\/\s*[A-DFXa-dfx][+-]?)?;?\s*/g;
  for (var r = 0; r < vals.length; r++) {
    var v = String(vals[r][0] || '');
    if (!v) continue;
    var nv = v.replace(/\[Rolly\]\s*/g, '').replace(gradeFrag, '$1')
              .replace(/^[;\s]+|[;\s]+$/g, '');
    vals[r][0] = nv;
  }
  rng.setValues(vals);
}

function deleteArchivedTabs() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.getSheets().forEach(function(sheet) {
    if (sheet.getName().indexOf('OLD ') === 0) ss.deleteSheet(sheet);
  });
}

// ── Import handler ─────────────────────────────────────────

function handleJobImport(data) {
  if (!tokenOk(data.token)) return jsonOut({ ok: false, error: 'unauthorized' });
  if (!data.show || !data.show.showId) return jsonOut({ ok: false, error: 'missing show.showId' });

  var lock = LockService.getScriptLock();
  try {
    // Queue behind onboarding posts / other chunks instead of failing
    lock.waitLock(60000);
  } catch (err) {
    return jsonOut({ ok: false, error: 'lock timeout' });
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var result = { ok: true, batchId: data.batchId, chunkIndex: data.chunkIndex };
  try {
    upsertShow(ss, data.show, data.batchId);

    var wr = getWorkRecordsSheet(ss);
    var existing = loadExistingHashes(wr, data.show.showId);
    var counts = appendWorkRecords(wr, data.records || [], existing,
                                   data.show, data.batchId, data.sourceFile);
    result.appended = counts.appended;
    result.skipped  = counts.skipped;

    if (data.finalize) {
      var summary = recomputeShowSummary(ss, data.show, data.people || []);
      result.peopleMatched = summary.matched;
      result.peopleCreated = summary.created;
      result.people = summary.people;
    }

    logImport(ss, {
      timestamp: new Date().toISOString(), batchId: data.batchId,
      chunkIndex: data.chunkIndex, chunkCount: data.chunkCount,
      showId: data.show.showId, received: (data.records || []).length,
      appended: counts.appended, skippedDupes: counts.skipped,
      finalize: !!data.finalize,
      peopleMatched: result.peopleMatched || '',
      peopleCreated: result.peopleCreated || '', error: ''
    });
    return jsonOut(result);

  } catch (err) {
    try {
      logImport(ss, {
        timestamp: new Date().toISOString(), batchId: data.batchId,
        chunkIndex: data.chunkIndex, chunkCount: data.chunkCount,
        showId: data.show.showId, received: (data.records || []).length,
        finalize: !!data.finalize, error: String(err)
      });
    } catch (e2) {}
    return jsonOut({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}
