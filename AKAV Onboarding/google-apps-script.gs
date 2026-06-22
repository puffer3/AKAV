// ============================================================
//  AKAV Crew Onboarding  →  Google Sheet + Drive
//  Paste into the Apps Script editor attached to your Sheet.
//  Deploy: Execute as Me · Anyone (even anonymous)
// ============================================================

var DRIVE_FOLDER_NAME = 'AKAV Onboarding Uploads';

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

// ── Main handler ───────────────────────────────────────────

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.tryLock(30000);
  try {
    var ss   = SpreadsheetApp.getActiveSpreadsheet();
    var data = JSON.parse(e.postData.contents);
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
    var tabName = isComplete(data) ? 'Submissions' : 'Incomplete Submissions';
    var sheet   = ss.getSheetByName(tabName) || ss.insertSheet(tabName);

    // First-time setup: write header row + formatting
    var isNewSheet = (sheet.getLastRow() === 0);
    if (isNewSheet) {
      sheet.appendRow(COLUMNS.map(function(c) { return c.header; }));
      applySheetFormatting(sheet);
    }

    // Build plain-value row (links/photos handled separately)
    var row = COLUMNS.map(function(col) { return formatValue(col, data); });

    var newRow = sheet.getLastRow() + 1;
    sheet.appendRow(row);

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

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}

// Open the /exec URL in a browser to confirm deployment is live
function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, msg: 'AKAV endpoint live' }))
    .setMimeType(ContentService.MimeType.JSON);
}
