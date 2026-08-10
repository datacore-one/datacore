const HOST_NAME = "com.datacore.tab_capture";

const FILTERED_PREFIXES = [
  "brave://", "chrome://", "about:", "chrome-extension://", "devtools://"
];

function isFiltered(url) {
  return FILTERED_PREFIXES.some(p => url.startsWith(p));
}

async function captureTabs() {
  const allTabs = await chrome.tabs.query({});
  const tabs = allTabs
    .filter(t => t.url && !isFiltered(t.url))
    .map(t => ({ title: t.title || t.url, url: t.url, id: t.id }));

  if (tabs.length === 0) {
    return { success: true, count: 0, duplicates_skipped: 0, tabIds: [] };
  }

  const message = {
    action: "capture",
    tabs: tabs.map(t => ({ title: t.title, url: t.url }))
  };

  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST_NAME, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      response.tabIds = tabs.map(t => t.id);
      resolve(response);
    });
  });
}

async function closeTabs(tabIds) {
  // Don't close the currently active tab in the current window
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const toClose = tabIds.filter(id => !activeTab || id !== activeTab.id);
  if (toClose.length > 0) {
    await chrome.tabs.remove(toClose);
  }
}

// Listen for messages from popup
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === "capture") {
    captureTabs()
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true; // async response
  }
  if (msg.action === "closeTabs") {
    closeTabs(msg.tabIds)
      .then(() => sendResponse({ success: true }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
});
