'use strict';

const state = {
  zeptoLoggedIn: null,
  order: null, // Full order from the API: the active one, or the one that just finished.
  orders: [], // Order history summaries.
  busy: null, // { label, startedAt } while a slow request runs.
  pendingMessage: null, // User message shown while Claude works on it.
  cartOpen: false,
  photo: null, // { file, url } waiting to be sent.
  loading: true,
};

const app = document.getElementById('app');
const statusDot = document.getElementById('status-dot');
const cancelButton = document.getElementById('cancel-button');
const reviewDialog = document.getElementById('review-dialog');
const reviewBody = document.getElementById('review-body');
const confirmDialog = document.getElementById('confirm-dialog');
const toastElement = document.getElementById('toast');

const ICONS = {
  camera:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
  send:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
};

class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function api(path, { method = 'GET', json, form } = {}) {
  const options = { method, headers: {} };
  if (json !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(json);
  } else if (form) {
    options.body = form;
  }

  let response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch {
    throw new ApiError('Could not reach the server.', 0, null);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.detail ?? `Request failed (${response.status}).`, response.status, body);
  }
  return body;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function money(paise) {
  if (paise === null || paise === undefined) return 'n/a';
  const rupees = paise / 100;
  return `₹${rupees.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

// Zepto names look like "Diet Coke  Can| Cola Sparkling Soft Drink | The Coca-Cola Company".
function shortName(name) {
  const first = String(name ?? '').split('|')[0].replace(/\s+/g, ' ').trim();
  return first || name;
}

// Item name with the quantity on its own line, so long names never split "x 3".
function itemLabel(name, quantity) {
  return `
    <span class="item-name">
      <span>${escapeHtml(shortName(name))}</span>
      <span class="item-quantity muted small-text">Qty ${quantity}</span>
    </span>`;
}

function formatDate(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return isoString;
  return date.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

function isActive(order) {
  return order && (order.status === 'open' || order.status === 'placing');
}

/* Feedback */

let toastTimer;
function showToast(message) {
  toastElement.textContent = message;
  toastElement.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastElement.hidden = true;
  }, 6000);
}

function confirmAction(text, yesLabel) {
  document.getElementById('confirm-text').textContent = text;
  document.getElementById('confirm-yes').textContent = yesLabel;
  confirmDialog.showModal();
  return new Promise((resolve) => {
    const finish = (answer) => {
      confirmDialog.close();
      resolve(answer);
    };
    document.getElementById('confirm-yes').onclick = () => finish(true);
    document.getElementById('confirm-no').onclick = () => finish(false);
    confirmDialog.oncancel = () => resolve(false);
  });
}

let busyTimer;
async function runBusy(label, task) {
  state.busy = { label, startedAt: Date.now() };
  render();
  busyTimer = setInterval(() => {
    const seconds = document.getElementById('working-seconds');
    if (seconds) seconds.textContent = Math.round((Date.now() - state.busy.startedAt) / 1000);
  }, 1000);
  try {
    return await task();
  } finally {
    clearInterval(busyTimer);
    state.busy = null;
    state.pendingMessage = null;
    render();
  }
}

/* Data loading */

async function loadOrders() {
  try {
    state.orders = await api('/orders');
  } catch (error) {
    showToast(error.message);
  }
}

async function refresh() {
  state.loading = true;
  render();
  try {
    const status = await api('/status');
    state.zeptoLoggedIn = status.zepto_logged_in;
    if (status.active_order_id) {
      state.order = await api(`/orders/${status.active_order_id}`);
    } else if (isActive(state.order)) {
      state.order = null;
    }
  } catch (error) {
    showToast(error.message);
  }
  await loadOrders();
  state.loading = false;
  render();
}

/* Actions */

async function startOrder() {
  try {
    await runBusy('Emptying your Zepto cart', async () => {
      state.order = await api('/orders', { method: 'POST' });
    });
    state.cartOpen = false;
    render();
  } catch (error) {
    showToast(error.message);
  }
}

async function sendMessage() {
  const textarea = document.getElementById('message-input');
  const text = textarea.value.trim();
  const photo = state.photo;
  if (!text && !photo) return;

  state.pendingMessage = { text, imageUrl: photo?.url };
  const label = photo ? 'Reading your list and filling the cart' : 'Updating your cart';
  try {
    await runBusy(label, async () => {
      if (photo) {
        const form = new FormData();
        form.append('photo', photo.file);
        form.append('text', text);
        state.order = await api(`/orders/${state.order.id}/photo`, { method: 'POST', form });
      } else {
        state.order = await api(`/orders/${state.order.id}/messages`, { method: 'POST', json: { text } });
      }
    });
    clearPhoto();
    render();
    document.getElementById('message-input').value = '';
  } catch (error) {
    // The draft is kept so it can be sent again. The server may have recorded the
    // message and an error reply before failing, so reload the chat.
    showToast(error.message);
    await reloadOrder();
  }
}

async function reloadOrder() {
  if (!state.order) return;
  try {
    state.order = await api(`/orders/${state.order.id}`);
  } catch (error) {
    showToast(error.message);
  }
  render();
}

async function compareOrder() {
  try {
    await runBusy('Checking Blinkit prices', async () => {
      const comparison = await api(`/orders/${state.order.id}/compare`, { method: 'POST' });
      state.order = { ...state.order, comparison };
    });
  } catch (error) {
    showToast(error.message);
  }
}

async function reviewOrder() {
  try {
    const review = await runBusy('Getting the Zepto total', () =>
      api(`/orders/${state.order.id}/review`, { method: 'POST' }),
    );
    openReview(review);
  } catch (error) {
    showToast(error.message);
  }
}

async function clearCart() {
  if (!(await confirmAction('Empty the cart? You can keep chatting after.', 'Clear cart'))) return;
  try {
    await runBusy('Clearing the cart', async () => {
      state.order = await api(`/orders/${state.order.id}/clear`, { method: 'POST' });
    });
  } catch (error) {
    showToast(error.message);
  }
}

async function cancelOrder() {
  if (!(await confirmAction('Cancel this order and empty the cart?', 'Cancel order'))) return;
  try {
    await runBusy('Cancelling', async () => {
      state.order = await api(`/orders/${state.order.id}/cancel`, { method: 'POST' });
    });
    await loadOrders();
    render();
  } catch (error) {
    showToast(error.message);
  }
}

function choosePhoto(file) {
  if (!file) return;
  clearPhoto();
  state.photo = { file, url: URL.createObjectURL(file) };
  render();
  document.getElementById('message-input')?.focus();
}

function clearPhoto() {
  if (state.photo) URL.revokeObjectURL(state.photo.url);
  state.photo = null;
}

/* Review and approve */

function openReview(review, notice = '') {
  const items = review.cart.items
    .map(
      (item) => `
        <tr>
          <td>${itemLabel(item.name, item.quantity)}</td>
          <td class="amount">${money(item.line_total_paise)}</td>
        </tr>`,
    )
    .join('');
  const itemTotal = review.cart.item_total_paise;
  const delivery = review.delivery_fee_paise ?? 0;
  const otherCharges = review.to_pay_paise - itemTotal - delivery;

  reviewBody.innerHTML = `
    <div class="stack">
      <h2>Review your order</h2>
      ${notice ? `<p class="notice">${escapeHtml(notice)}</p>` : ''}
      <table class="bill">
        <tbody>
          ${items}
          <tr><td class="muted">Items</td><td class="amount">${money(itemTotal)}</td></tr>
          <tr><td class="muted">Delivery</td><td class="amount">${money(delivery)}</td></tr>
          ${otherCharges > 0 ? `<tr><td class="muted">Other charges</td><td class="amount">${money(otherCharges)}</td></tr>` : ''}
          ${otherCharges < 0 ? `<tr><td class="muted">Discounts</td><td class="amount">-${money(-otherCharges)}</td></tr>` : ''}
          <tr class="total"><td>To pay on delivery</td><td class="amount">${money(review.to_pay_paise)}</td></tr>
        </tbody>
      </table>
      <p class="muted small-text">This goes to Zepto as a cash on delivery order for your home address.</p>
      <div class="row end wrap">
        <button type="button" class="button ghost" data-review="close">Back</button>
        <button type="button" class="button" data-review="place">Place order (${money(review.to_pay_paise)})</button>
      </div>
    </div>`;

  reviewBody.querySelector('[data-review="close"]').onclick = () => reviewDialog.close();
  reviewBody.querySelector('[data-review="place"]').onclick = (event) =>
    placeOrder(review, event.currentTarget);
  if (!reviewDialog.open) reviewDialog.showModal();
}

async function placeOrder(review, button) {
  button.disabled = true;
  button.textContent = 'Placing order...';
  try {
    state.order = await api(`/orders/${state.order.id}/approve`, {
      method: 'POST',
      json: { review_token: review.token },
    });
    reviewDialog.close();
    await loadOrders();
    render();
  } catch (error) {
    if (error.status === 409 && error.body?.review) {
      openReview(error.body.review, 'Something in the cart changed since you opened this. Have another look before ordering.');
      return;
    }
    // Reload this order rather than going home, so a failed placement keeps its
    // "may not have gone through" warning on screen.
    reviewDialog.close();
    showToast(error.message);
    await reloadOrder();
    await loadOrders();
    render();
  }
}

/* Rendering */

function render() {
  // Rendering replaces the composer, so carry over anything typed so far.
  const draft = document.getElementById('message-input')?.value ?? '';

  if (state.loading && !state.order) {
    app.innerHTML = '<p class="muted">Loading...</p>';
  } else if (isActive(state.order)) {
    app.innerHTML = renderOrder(state.order);
  } else {
    app.innerHTML = renderHome();
  }

  cancelButton.hidden = state.order?.status !== 'open';
  cancelButton.disabled = Boolean(state.busy);
  statusDot.className = `status-dot ${state.zeptoLoggedIn === null ? '' : state.zeptoLoggedIn ? 'ok' : 'bad'}`;
  statusDot.title = state.zeptoLoggedIn ? 'Zepto connected' : 'Zepto not logged in';

  const textarea = document.getElementById('message-input');
  if (textarea) {
    textarea.value = draft;
    textarea.addEventListener('keydown', onComposerKey);
  }
  if (isActive(state.order)) window.scrollTo({ top: document.body.scrollHeight });
}

function renderHome() {
  const loginNotice =
    state.zeptoLoggedIn === false
      ? '<p class="notice">Zepto isn\'t logged in. Run <code>scripts/zepto_login.py</code> on the server.</p>'
      : '';
  const busy = state.busy ? renderWorking() : '';

  return `
    ${renderFinishedOrder(state.order)}
    <section class="card stack">
      <h1>New order</h1>
      <p class="muted">Type out what you need, or take a photo of your list. Every order starts
        with an empty Zepto cart, and you pay cash when it arrives.</p>
      ${loginNotice}
      ${busy}
      <button type="button" class="button block" data-action="start" ${state.busy ? 'disabled' : ''}>
        Start new order
      </button>
    </section>
    ${renderHistory()}`;
}

function renderFinishedOrder(order) {
  if (!order) return '';
  if (order.status === 'placed') {
    const text = order.zepto_response?.text ?? '';
    return `
      <section class="card stack">
        <div class="row between"><h2>Order placed</h2><span class="badge placed">Cash on delivery</span></div>
        <p>Zepto has your order. Keep ${money(order.review?.to_pay_paise)} in cash ready for the delivery.</p>
        ${text ? `<details><summary>Zepto's confirmation</summary><p class="small-text muted" style="white-space: pre-wrap">${escapeHtml(text)}</p></details>` : ''}
      </section>`;
  }
  if (order.status === 'failed') {
    return `
      <section class="card stack">
        <h2>Order may not have gone through</h2>
        <p class="notice">${escapeHtml(order.error ?? 'Something went wrong while placing the order.')}</p>
      </section>`;
  }
  return '';
}

function renderHistory() {
  if (!state.orders.length) return '';
  const rows = state.orders
    .slice(0, 15)
    .map(
      (order) => `
        <li>
          <span>
            <span>Order #${order.id}</span><br>
            <span class="muted small-text">${escapeHtml(formatDate(order.created_at))}</span>
          </span>
          <span class="stack" style="gap: 4px; align-items: flex-end">
            <span class="badge ${escapeHtml(order.status)}">${escapeHtml(order.status)}</span>
            ${order.review ? `<span class="amount small-text">${money(order.review.to_pay_paise)}</span>` : ''}
          </span>
        </li>`,
    )
    .join('');
  return `
    <section class="card">
      <h2>Recent orders</h2>
      <ul class="order-list">${rows}</ul>
    </section>`;
}

function renderOrder(order) {
  if (order.status === 'placing') {
    return `
      <section class="card stack">
        <h2>Placing your order</h2>
        <p class="muted">Still waiting to hear back from Zepto. If nothing changes in a minute or
          so, check the Zepto app before you order again.</p>
        <div class="row wrap">
          <button type="button" class="button ghost" data-action="refresh">Refresh</button>
          <button type="button" class="button" data-action="start">Start new order</button>
        </div>
      </section>`;
  }
  return `
    <section class="chat">${renderMessages(order)}</section>
    ${order.comparison ? renderComparison(order.comparison) : ''}
    ${renderDock(order)}`;
}

function renderMessages(order) {
  const messages = order.messages.map((message) =>
    renderBubble(
      message.role,
      message.text,
      message.image_file ? `/api/uploads/${encodeURIComponent(message.image_file)}` : null,
    ),
  );
  if (state.pendingMessage) {
    messages.push(renderBubble('user', state.pendingMessage.text, state.pendingMessage.imageUrl));
  }
  if (state.busy) messages.push(renderWorking());

  if (!messages.length) {
    return `
      <div class="empty-chat muted">
        <p><strong>Nothing in the cart yet.</strong></p>
        <p>Try something like "2 litres of milk, a dozen eggs and some brown bread". You can also tap the camera and snap your list.</p>
      </div>`;
  }
  return messages.join('');
}

function renderBubble(role, text, imageUrl) {
  const image = imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="Grocery list photo">` : '';
  return `<div class="bubble ${role === 'user' ? 'user' : 'assistant'}">${image}${escapeHtml(text)}</div>`;
}

function renderWorking() {
  const seconds = state.busy ? Math.round((Date.now() - state.busy.startedAt) / 1000) : 0;
  return `
    <div class="working" role="status">
      <span class="spinner"></span>
      <span>${escapeHtml(state.busy?.label ?? 'Working')}... <span id="working-seconds">${seconds}</span>s</span>
    </div>`;
}

function renderDock(order) {
  const cart = order.cart ?? { items: [], item_total_paise: 0 };
  const count = cart.items.reduce((sum, item) => sum + item.quantity, 0);
  const disabled = state.busy ? 'disabled' : '';
  const emptyCart = cart.items.length === 0 ? 'disabled' : '';

  const cartList = state.cartOpen
    ? `<div class="card stack">
        ${
          cart.items.length
            ? `<ul class="cart-items">${cart.items
                .map(
                  (item) => `
                    <li>
                      ${itemLabel(item.name, item.quantity)}
                      <span class="amount">${money(item.line_total_paise)}</span>
                    </li>`,
                )
                .join('')}</ul>
              <div class="row end">
                <button type="button" class="button text-danger compact" data-action="clear" ${disabled}>Clear cart</button>
              </div>`
            : '<p class="muted">Nothing in the cart yet.</p>'
        }
      </div>`
    : '';

  const photoPreview = state.photo
    ? `<div class="photo-preview">
        <img src="${escapeHtml(state.photo.url)}" alt="Selected photo">
        <span class="muted small-text">Photo ready to send</span>
        <button type="button" class="button ghost" data-action="remove-photo">Remove</button>
      </div>`
    : '';

  return `
    <div class="dock">
      <button type="button" class="cart-summary" data-action="toggle-cart" aria-expanded="${state.cartOpen}">
        <span><strong>Cart</strong> <span class="muted">(${count} item${count === 1 ? '' : 's'})</span></span>
        <span class="amount">${money(cart.item_total_paise)} <span class="muted small-text">${state.cartOpen ? 'Hide' : 'Show'}</span></span>
      </button>
      ${cartList}
      ${
        state.cartOpen
          ? `<div class="actions">
              <button type="button" class="button ghost" data-action="compare" ${disabled || emptyCart}>Compare prices</button>
              <button type="button" class="button" data-action="review" ${disabled || emptyCart}>Review &amp; order</button>
            </div>`
          : ''
      }
      ${photoPreview}
      <form class="composer" data-action="send">
        <label class="icon-button" title="Take a photo of your list">
          ${ICONS.camera}
          <span class="visually-hidden">Take a photo of your list</span>
          <input type="file" accept="image/*" capture="environment" class="photo-input visually-hidden" ${disabled}>
        </label>
        <label class="visually-hidden" for="message-input">Message</label>
        <textarea id="message-input" rows="1" placeholder="Add items..." ${disabled}></textarea>
        <button type="submit" class="icon-button" title="Send" ${disabled}>
          ${ICONS.send}
          <span class="visually-hidden">Send</span>
        </button>
      </form>
    </div>`;
}

function renderComparison(comparison) {
  const zeptoWins = comparison.cheaper === 'zepto';
  const blinkitWins = comparison.cheaper === 'blinkit';
  let headline = "Couldn't get Blinkit prices.";
  if (comparison.cheaper === 'same') headline = 'Both cost the same.';
  else if (zeptoWins) headline = `Zepto is ${money(comparison.difference_paise)} cheaper.`;
  else if (blinkitWins) headline = `Blinkit is ${money(comparison.difference_paise)} cheaper.`;

  const missing = comparison.unmatched_items + (comparison.blinkit.unavailable_items ?? 0);
  const incomplete = comparison.complete
    ? ''
    : `<p class="notice">Blinkit doesn't have ${missing === 1 ? 'one of these items' : `${missing} of
        these items`}, so the totals aren't a fair comparison.</p>`;

  const matches = comparison.items
    .map((row) => {
      const blinkit = row.blinkit
        ? `<span>Blinkit: ${escapeHtml(row.blinkit.name)}</span>
           <span class="muted nowrap">${escapeHtml(row.blinkit.unit)}, Qty ${row.blinkit.quantity}</span>`
        : '<span class="muted">Not found on Blinkit</span>';
      return `
        <li>
          <div class="row between">
            ${itemLabel(row.zepto.name, row.zepto.quantity)}
            <span class="amount">${money(row.zepto.line_total_paise)}</span>
          </div>
          <div class="row between">
            <span class="item-name small-text">${blinkit}</span>
            <span class="amount small-text">${row.blinkit ? money(row.blinkit.line_total_paise) : ''}</span>
          </div>
          ${row.note && !row.exact ? `<div class="muted small-text">${escapeHtml(row.note)}</div>` : ''}
        </li>`;
    })
    .join('');

  return `
    <section class="card stack">
      <div class="row between">
        <h2>Price check</h2>
        ${comparison.cheaper && comparison.cheaper !== 'same' ? `<span class="badge cheaper">${zeptoWins ? 'Zepto' : 'Blinkit'} cheaper</span>` : ''}
      </div>
      <div class="compare-totals">
        ${renderTotalBox('Zepto', comparison.zepto, zeptoWins)}
        ${renderTotalBox('Blinkit', comparison.blinkit, blinkitWins)}
      </div>
      <p>${escapeHtml(headline)}</p>
      ${incomplete}
      <p class="muted small-text">Both totals include delivery and fees. Blinkit's come from its
        website, and you can only order from Zepto for now.</p>
      <details>
        <summary>Item matches</summary>
        <ul class="match-list">${matches}</ul>
      </details>
    </section>`;
}

function renderTotalBox(name, totals, isWinner) {
  return `
    <div class="total-box ${isWinner ? 'winner' : ''}">
      <div class="muted small-text">${name}</div>
      <div class="big">${money(totals.to_pay_paise)}</div>
      <div class="muted small-text">Delivery ${money(totals.delivery_fee_paise)}</div>
    </div>`;
}

/* Events */

function onComposerKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
}

app.addEventListener('click', (event) => {
  const target = event.target.closest('[data-action]');
  if (!target || target.tagName === 'FORM') return;
  const actions = {
    start: startOrder,
    compare: compareOrder,
    review: reviewOrder,
    clear: clearCart,
    refresh,
    'toggle-cart': () => {
      state.cartOpen = !state.cartOpen;
      render();
    },
    'remove-photo': () => {
      clearPhoto();
      render();
    },
  };
  actions[target.dataset.action]?.();
});

app.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage();
});

app.addEventListener('change', (event) => {
  if (event.target.matches('.photo-input')) choosePhoto(event.target.files[0]);
});

document.getElementById('home-button').addEventListener('click', refresh);
cancelButton.addEventListener('click', cancelOrder);

refresh();
