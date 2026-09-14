// Standalone IF Player shell -- the JS half of the UI.
//
// Every call here goes through window.pywebview.api.<name>(...), which
// returns a Promise resolving to the JSON-safe dict PlayerAPI
// (if_player/player_api.py) builds. This script owns rendering only --
// no game logic lives here.

(function () {
  "use strict";

  var state = {
    gameDir: null,
    turnCount: -1,
    playLayout: "classic",
    readerPrefs: {font_size: "medium", text_width: "medium"},
  };

  var els = {};

  function bindElements() {
    [
      "library-view", "open-folder-button", "library-error",
      "trust-prompt", "trust-folder-name", "trust-decline", "trust-accept",
      "play-view", "sidebar", "back-to-library", "undo-button", "restart-button",
      "saves-button", "save-status", "settings-button",
      "story-column", "story-title", "centre-header", "centre-footer",
      "transcript", "current-turn-divider", "current-turn",
      "story-images", "story-text", "story-done", "done-library-button",
      "story-stalled", "choices-box", "choices-list",
      "game-panel", "panel-tabs", "panel-sections", "panel-detail",
      "saves-modal", "saves-close", "saves-list",
      "settings-modal", "settings-close",
      "game-prose-styles",
    ].forEach(function (id) {
      els[camelCase(id)] = document.getElementById(id);
    });
  }

  function camelCase(id) {
    return id.replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
  }

  function api() {
    return window.pywebview.api;
  }

  // -- Story-text escaping ------------------------------------------------
  //
  // Story/transcript/panel-detail text is escaped then a small allow-list
  // of inline tags restored: a game's prose is trusted content but still
  // runs through one shared escape so a malformed or hostile tag can
  // never break the page or inject an attribute. This is the one script
  // a hostile game folder's own DOM ends up running in, so panel text
  // gets the exact same allow-listed-tags rule as story prose rather
  // than being trusted raw.
  var ALLOWED_INLINE_TAGS = ["i", "b", "em", "strong", "br"];

  // <style=name>...</style> is this project's own inline-tag convention
  // (not part of the Ink language) for naming a prose voice -- font,
  // size, colour -- defined as CSS in style.css and/or a game's own
  // styles.css (see openGame()). The name is restricted to
  // letters/digits/hyphen/underscore and turned into a fixed "style-"
  // prefixed class, never into a raw attribute value, so a malformed or
  // hostile name can never break out of the class attribute.
  var STYLE_TAG_OPEN_RE = /&lt;style=([A-Za-z0-9_-]+)&gt;/gi;
  var STYLE_TAG_CLOSE_RE = /&lt;\/style&gt;/gi;

  function storyHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    var escaped = div.innerHTML;
    ALLOWED_INLINE_TAGS.forEach(function (tag) {
      var openRe = new RegExp("&lt;" + tag + "&gt;", "gi");
      var closeRe = new RegExp("&lt;/" + tag + "&gt;", "gi");
      escaped = escaped.replace(openRe, "<" + tag + ">").replace(closeRe, "</" + tag + ">");
    });
    escaped = escaped.replace(STYLE_TAG_OPEN_RE, function (_, name) {
      return '<span class="style-' + name + '">';
    });
    escaped = escaped.replace(STYLE_TAG_CLOSE_RE, "</span>");
    return escaped;
  }

  function paragraphs(text) {
    return (text || "").split("\n").filter(Boolean).map(function (p) {
      return "<p>" + storyHtml(p) + "</p>";
    }).join("");
  }

  // -- Library view ---------------------------------------------------

  function showLibrary(errorMessage) {
    els.libraryView.hidden = false;
    els.trustPrompt.hidden = true;
    els.playView.hidden = true;
    if (errorMessage) {
      els.libraryError.textContent = errorMessage;
      els.libraryError.hidden = false;
    } else {
      els.libraryError.hidden = true;
    }
  }

  function openFolderPicker() {
    // pywebview's own native folder dialog -- exposed as a second js_api
    // method (create_file_dialog wraps webview.windows[0].create_file_dialog
    // with FOLDER_DIALOG) rather than reimplemented in JS, since only the
    // Python side can show a native OS dialog.
    api().pick_game_folder().then(function (folder) {
      if (folder) openGame(folder);
    });
  }

  function openGame(gameDir) {
    state.gameDir = gameDir;
    api().open_game(gameDir).then(function (context) {
      if (context.error) {
        showLibrary(context.error);
        return;
      }
      if (context.needs_trust) {
        showTrustPrompt(gameDir);
        return;
      }
      state.playLayout = context.play_layout || "classic";
      state.readerPrefs = context.reader_prefs || state.readerPrefs;
      els.gameProseStyles.textContent = context.prose_styles || "";
      applyBodyClasses();
      showPlayView();
      renderTurn(context);
    });
  }

  // -- Trust prompt -----------------------------------------------------

  function showTrustPrompt(gameDir) {
    els.libraryView.hidden = true;
    els.playView.hidden = true;
    els.trustFolderName.textContent = gameDir;
    els.trustPrompt.hidden = false;
  }

  function acceptTrust() {
    api().confirm_trust(state.gameDir).then(function () {
      openGame(state.gameDir);
    });
  }

  function declineTrust() {
    // A plugin-requiring game keeps re-prompting each session until
    // trusted, rather than playing with missing capabilities silently
    // granted.
    showLibrary(null);
  }

  // -- Play view chrome -------------------------------------------------

  function applyBodyClasses() {
    document.body.className = [
      "layout-" + state.playLayout,
      "if-font-" + state.readerPrefs.font_size,
      "if-width-" + state.readerPrefs.text_width,
    ].join(" ");
    els.gamePanel.hidden = state.playLayout !== "three_column";
    els.centreHeader.hidden = state.playLayout !== "three_column";
    els.centreFooter.hidden = state.playLayout !== "three_column";
  }

  function showPlayView() {
    els.libraryView.hidden = true;
    els.trustPrompt.hidden = true;
    els.playView.hidden = false;
  }

  function backToLibrary() {
    state.gameDir = null;
    showLibrary(null);
  }

  // -- Turn rendering -----------------------------------------------------

  function renderTurn(context) {
    state.turnCount = context.turn_count;

    renderTranscript(context.transcript || []);
    renderImages(context.image_urls || []);
    els.storyText.innerHTML = paragraphs(context.text);

    els.storyDone.hidden = !context.done;
    var hasChoices = !context.done && (context.choices || []).length > 0;
    els.choicesBox.hidden = !hasChoices;
    els.storyStalled.hidden = context.done || hasChoices;
    if (hasChoices) renderChoices(context.choices);

    els.undoButton.hidden = !context.can_undo;

    renderPanel(context.panel);
  }

  function renderTranscript(transcript) {
    if (transcript.length <= 1) {
      els.transcript.innerHTML = "";
      els.currentTurnDivider.hidden = true;
      return;
    }
    var html = "";
    transcript.slice(0, -1).forEach(function (entry, index) {
      if (index > 0) html += "<hr>";
      if (entry.chosen_label) html += '<p class="transcript-choice">&gt; ' + storyHtml(entry.chosen_label) + "</p>";
      html += paragraphs(entry.text);
    });
    els.transcript.innerHTML = html;
    els.currentTurnDivider.hidden = false;
  }

  function renderImages(urls) {
    els.storyImages.innerHTML = urls.map(function (url) {
      return '<img src="' + url + '" alt="">';
    }).join("");
  }

  function renderChoices(choices) {
    els.choicesList.innerHTML = "";
    choices.forEach(function (pair) {
      var index = pair[0];
      var text = pair[1];
      var button = document.createElement("button");
      button.className = "choice-button";
      button.dataset.choiceKey = String(index + 1);
      button.innerHTML = storyHtml(text) + '<span class="choice-key">' + (index + 1) + "</span>";
      button.addEventListener("click", function () { submitChoice(index); });
      els.choicesList.appendChild(button);
    });
  }

  function submitChoice(index) {
    api().choose(index, state.turnCount).then(function (context) {
      if (context.stale) { reopenAfterStale(); return; }
      if (context.error) { return; }
      renderTurn(context);
    });
  }

  function reopenAfterStale() {
    // The turn moved on under us -- re-fetch the live state rather than
    // trying to reconcile a stale response by hand.
    if (state.gameDir) openGame(state.gameDir);
  }

  function undoTurn() {
    api().undo().then(function (context) {
      if (context.error) return;
      renderTurn(context);
    });
  }

  function restartGame() {
    if (!window.confirm("Restart this story? Your current progress will be lost.")) return;
    api().restart().then(function (context) {
      if (context.error) return;
      renderTurn(context);
    });
  }

  // -- Quicksave / quickload (F5 / F9) -----------------------------------
  //
  // One dedicated slot, no label prompt, shown via a small transient
  // toast in the sidebar rather than a dialog -- the instant,
  // low-ceremony feel F5/F9 has in other desktop games.

  var saveStatusTimer = null;

  function showSaveStatus(message) {
    els.saveStatus.textContent = message;
    els.saveStatus.hidden = false;
    if (saveStatusTimer) clearTimeout(saveStatusTimer);
    saveStatusTimer = setTimeout(function () { els.saveStatus.hidden = true; }, 2500);
  }

  function quicksave() {
    api().quicksave().then(function (result) {
      showSaveStatus(result.error ? result.error : "Quicksaved");
    });
  }

  function quickload() {
    api().quickload().then(function (context) {
      if (context.error) { showSaveStatus(context.error); return; }
      renderTurn(context);
      showSaveStatus("Quickloaded");
    });
  }

  // -- Named save-slot manager ---------------------------------------

  function openSavesModal() {
    refreshSavesList();
    els.savesModal.hidden = false;
  }

  function closeSavesModal() {
    els.savesModal.hidden = true;
  }

  function refreshSavesList() {
    api().list_saves().then(function (result) {
      renderSavesList(result.slots || []);
    });
  }

  function renderSavesList(slots) {
    els.savesList.innerHTML = "";
    slots.forEach(function (slot) {
      els.savesList.appendChild(renderSaveSlotRow(slot));
    });
  }

  function renderSaveSlotRow(slot) {
    var row = document.createElement("li");
    row.className = "save-slot-row";

    var info = document.createElement("div");
    info.className = "save-slot-info";
    var labelLine = document.createElement("div");
    labelLine.className = "slot-label";
    labelLine.textContent = slot.used ? (slot.label || "Slot " + (slot.slot + 1)) : "Slot " + (slot.slot + 1) + " (empty)";
    info.appendChild(labelLine);
    if (slot.used) {
      var metaLine = document.createElement("div");
      metaLine.className = "slot-meta";
      metaLine.textContent = "Turn " + slot.turn_count;
      info.appendChild(metaLine);
    }
    row.appendChild(info);

    var saveButton = document.createElement("button");
    saveButton.textContent = "Save";
    saveButton.addEventListener("click", function () { saveToSlotPrompted(slot.slot); });
    row.appendChild(saveButton);

    if (slot.used) {
      var loadButton = document.createElement("button");
      loadButton.textContent = "Load";
      loadButton.addEventListener("click", function () { loadFromSlot(slot.slot); });
      row.appendChild(loadButton);

      var exportButton = document.createElement("button");
      exportButton.textContent = "Export";
      exportButton.addEventListener("click", function () { exportSlot(slot.slot); });
      row.appendChild(exportButton);

      var deleteButton = document.createElement("button");
      deleteButton.textContent = "Delete";
      deleteButton.addEventListener("click", function () { deleteSlotConfirmed(slot.slot); });
      row.appendChild(deleteButton);
    }

    var importButton = document.createElement("button");
    importButton.textContent = "Import";
    importButton.addEventListener("click", function () { importIntoSlot(slot.slot); });
    row.appendChild(importButton);

    return row;
  }

  function saveToSlotPrompted(slot) {
    var label = window.prompt("Label for this save (optional):", "") || "";
    api().save_to_slot(slot, label).then(function (result) {
      if (result.error) { showSaveStatus(result.error); return; }
      refreshSavesList();
    });
  }

  function loadFromSlot(slot) {
    api().load_from_slot(slot).then(function (context) {
      if (context.error) { showSaveStatus(context.error); return; }
      renderTurn(context);
      closeSavesModal();
    });
  }

  function deleteSlotConfirmed(slot) {
    if (!window.confirm("Delete this save? This cannot be undone.")) return;
    api().delete_save(slot).then(function (result) {
      if (result.error) { showSaveStatus(result.error); return; }
      refreshSavesList();
    });
  }

  function exportSlot(slot) {
    api().pick_save_destination("save-slot-" + (slot + 1) + ".json").then(function (destination) {
      if (!destination) return;
      api().export_save(slot, destination).then(function (result) {
        showSaveStatus(result.error ? result.error : "Exported");
      });
    });
  }

  function importIntoSlot(slot) {
    api().pick_save_file().then(function (source) {
      if (!source) return;
      api().import_save(slot, source).then(function (result) {
        if (result.error) { showSaveStatus(result.error); return; }
        refreshSavesList();
      });
    });
  }

  // -- Reader preferences -------------------------------------------------
  //
  // Each radio change is its own set_reader_prefs() call, applied to the
  // live page immediately via applyBodyClasses() -- no separate save/
  // close step.

  function openSettingsModal() {
    document.querySelectorAll('#settings-modal input[name="font-size"]').forEach(function (input) {
      input.checked = input.value === state.readerPrefs.font_size;
    });
    document.querySelectorAll('#settings-modal input[name="text-width"]').forEach(function (input) {
      input.checked = input.value === state.readerPrefs.text_width;
    });
    els.settingsModal.hidden = false;
  }

  function closeSettingsModal() {
    els.settingsModal.hidden = true;
  }

  function onReaderPrefChanged() {
    var fontSize = document.querySelector('#settings-modal input[name="font-size"]:checked');
    var textWidth = document.querySelector('#settings-modal input[name="text-width"]:checked');
    api().set_reader_prefs(fontSize ? fontSize.value : state.readerPrefs.font_size, textWidth ? textWidth.value : state.readerPrefs.text_width).then(
      function (result) {
        state.readerPrefs = result.prefs;
        applyBodyClasses();
      }
    );
  }

  // -- Game panel --------------------------------------------------------

  function renderPanel(panel) {
    if (!panel) {
      els.panelTabs.hidden = true;
      els.panelTabs.innerHTML = "";
      els.panelSections.innerHTML = "";
      els.panelDetail.innerHTML = "";
      return;
    }

    var tabs = panel.panel_tabs || [];
    if (tabs.length > 1) {
      els.panelTabs.hidden = false;
      els.panelTabs.innerHTML = "";
      tabs.forEach(function (tab) {
        var button = document.createElement("button");
        button.textContent = tab.label;
        button.title = tab.label;
        if (tab.id === panel.panel_active_tab) button.className = "is-active";
        button.addEventListener("click", function () { switchPanelTab(tab.id); });
        els.panelTabs.appendChild(button);
      });
    } else {
      els.panelTabs.hidden = true;
      els.panelTabs.innerHTML = "";
    }

    els.panelSections.innerHTML = "";
    (panel.panel_sections || []).forEach(function (section) {
      els.panelSections.appendChild(renderPanelSection(section));
    });
    renderPanelDetail(panel.panel_detail || "");
  }

  // Built via DOM APIs rather than string-concatenated HTML: action.id/
  // action.item/action.kind/action.title are game-authored strings, and
  // going through element.dataset/title/textContent (rather than
  // interpolating into an HTML attribute string) means there is no
  // attribute-escaping step to get wrong -- the DOM API itself never
  // parses these values as markup.
  function renderPanelSection(section) {
    var container = document.createElement("div");
    container.className = "panel-section";
    if (section.heading) {
      var heading = document.createElement("h3");
      heading.className = "panel-heading";
      heading.textContent = section.heading;
      container.appendChild(heading);
    }
    if (section.rows && section.rows.length) {
      var list = document.createElement("ul");
      list.className = "panel-list";
      section.rows.forEach(function (row) {
        list.appendChild(renderPanelRow(row));
      });
      container.appendChild(list);
    } else if (section.empty_text) {
      var empty = document.createElement("p");
      empty.className = "panel-empty";
      empty.textContent = section.empty_text;
      container.appendChild(empty);
    }
    return container;
  }

  function renderPanelRow(row) {
    var item = document.createElement("li");
    item.className = "panel-row" + (row.emphasis ? " panel-row-emphasis" : "");

    var label = document.createElement("span");
    label.className = "panel-row-label";
    label.textContent = row.label;
    if (row.badge) {
      var badge = document.createElement("span");
      badge.className = "panel-badge";
      badge.textContent = row.badge;
      label.appendChild(document.createTextNode(" "));
      label.appendChild(badge);
    }
    item.appendChild(label);

    if (row.actions && row.actions.length) {
      var actions = document.createElement("span");
      actions.className = "panel-actions";
      row.actions.forEach(function (action) {
        actions.appendChild(renderPanelActionButton(action));
      });
      item.appendChild(actions);
    }
    return item;
  }

  function renderPanelActionButton(action) {
    var button = document.createElement("button");
    var label = action.title || action.id;
    button.textContent = label;
    button.title = label;
    var kind = action.kind || "action";
    button.addEventListener("click", function () {
      if (kind === "command") {
        runPanelCommand(action.id, action.item);
      } else {
        runPanelAction(action.id, action.item);
      }
    });
    return button;
  }

  function runPanelAction(actionId, target) {
    api().panel_action(actionId, target).then(function (result) {
      renderPanelDetail(result.detail_text || "");
    });
  }

  function runPanelCommand(commandId, target) {
    api().panel_command(commandId, target, state.turnCount).then(function (context) {
      if (context.stale) { reopenAfterStale(); return; }
      renderPanel(context);
    });
  }

  function switchPanelTab(tabId) {
    api().panel_tab(tabId).then(renderPanel);
  }

  function renderPanelDetail(detailText) {
    els.panelDetail.innerHTML = storyHtml(detailText);
  }

  // -- Wiring --------------------------------------------------------

  function init() {
    bindElements();
    els.openFolderButton.addEventListener("click", openFolderPicker);
    els.trustAccept.addEventListener("click", acceptTrust);
    els.trustDecline.addEventListener("click", declineTrust);
    els.backToLibrary.addEventListener("click", backToLibrary);
    els.doneLibraryButton.addEventListener("click", backToLibrary);
    els.undoButton.addEventListener("click", undoTurn);
    els.restartButton.addEventListener("click", restartGame);
    els.savesButton.addEventListener("click", openSavesModal);
    els.savesClose.addEventListener("click", closeSavesModal);
    els.settingsButton.addEventListener("click", openSettingsModal);
    els.settingsClose.addEventListener("click", closeSettingsModal);
    document.querySelectorAll('#settings-modal input[type="radio"]').forEach(function (input) {
      input.addEventListener("change", onReaderPrefChanged);
    });

    document.addEventListener("keydown", function (event) {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || "")) return;
      if (event.key === "F5") { event.preventDefault(); quicksave(); return; }
      if (event.key === "F9") { event.preventDefault(); quickload(); return; }
      if (!els.playView.hidden) {
        var button = els.choicesList.querySelector('[data-choice-key="' + event.key + '"]');
        if (button) button.click();
      }
    });

    showLibrary(null);
  }

  if (window.pywebview) {
    init();
  } else {
    // pywebview injects window.pywebview asynchronously after the page
    // loads -- the documented `pywebviewready` event is the real signal
    // to wait for, rather than polling.
    window.addEventListener("pywebviewready", init);
  }
})();
