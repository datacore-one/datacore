const HOST_NAME = "com.datacore.tab_capture";
const FILTERED_PREFIXES = [
  "brave://", "chrome://", "about:", "chrome-extension://", "devtools://"
];

const capturePhase = document.getElementById("phase-capture");
const closePhase = document.getElementById("phase-close");
const donePhase = document.getElementById("phase-done");
const captureStatus = document.getElementById("capture-status");
const closeStatus = document.getElementById("close-status");
const doneStatus = document.getElementById("done-status");
const captureBtn = document.getElementById("capture-btn");
const closeYes = document.getElementById("close-yes");
const closeNo = document.getElementById("close-no");

let capturedTabIds = [];

function isFiltered(url) {
  return FILTERED_PREFIXES.some(p => url.startsWith(p));
}

captureBtn.addEventListener("click", async () => {
  captureBtn.disabled = true;
  captureStatus.textContent = "Capturing...";
  captureStatus.className = "status";

  try {
    const allTabs = await chrome.tabs.query({});
    const tabs = allTabs
      .filter(t => t.url && !isFiltered(t.url))
      .map(t => ({ title: t.title || t.url, url: t.url, id: t.id }));

    if (tabs.length === 0) {
      capturePhase.classList.add("hidden");
      donePhase.classList.remove("hidden");
      doneStatus.textContent = "No tabs to capture.";
      return;
    }

    const message = {
      action: "capture",
      tabs: tabs.map(t => ({ title: t.title, url: t.url }))
    };

    chrome.runtime.sendNativeMessage(HOST_NAME, message, (result) => {
      if (chrome.runtime.lastError) {
        captureStatus.textContent = chrome.runtime.lastError.message;
        captureStatus.className = "status error";
        captureBtn.disabled = false;
        return;
      }

      if (!result || !result.success) {
        captureStatus.textContent = result?.error || "Capture failed";
        captureStatus.className = "status error";
        captureBtn.disabled = false;
        return;
      }

      capturedTabIds = tabs.map(t => t.id);
      const parts = [];
      if (result.count > 0) parts.push(`${result.count} captured`);
      if (result.duplicates_skipped > 0) parts.push(`${result.duplicates_skipped} skipped`);
      const summary = parts.join(", ");

      if (result.count > 0 && capturedTabIds.length > 0) {
        capturePhase.classList.add("hidden");
        closePhase.classList.remove("hidden");
        closeStatus.textContent = `${summary}. Close captured tabs?`;
      } else {
        capturePhase.classList.add("hidden");
        donePhase.classList.remove("hidden");
        doneStatus.textContent = summary + ".";
      }
    });
  } catch (err) {
    captureStatus.textContent = err.message;
    captureStatus.className = "status error";
    captureBtn.disabled = false;
  }
});

closeYes.addEventListener("click", async () => {
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const toClose = capturedTabIds.filter(id => !activeTab || id !== activeTab.id);
  if (toClose.length > 0) {
    await chrome.tabs.remove(toClose);
  }
  closePhase.classList.add("hidden");
  donePhase.classList.remove("hidden");
  doneStatus.textContent = "Done. Tabs closed.";
  setTimeout(() => window.close(), 1000);
});

closeNo.addEventListener("click", () => {
  closePhase.classList.add("hidden");
  donePhase.classList.remove("hidden");
  doneStatus.textContent = "Done. Tabs kept open.";
  setTimeout(() => window.close(), 1000);
});
