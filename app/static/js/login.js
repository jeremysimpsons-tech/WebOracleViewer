const token = document.querySelector('meta[name="csrf-token"]').content;

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("login-error");
  const button = document.getElementById("login-submit");
  error.textContent = "";
  button.disabled = true;
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
      },
      body: JSON.stringify({ password: document.getElementById("password").value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      error.textContent = typeof data.detail === "string" ? data.detail : "Sign-in failed.";
      return;
    }
    window.location.assign("/");
  } catch (err) {
    error.textContent = "The service did not respond.";
  } finally {
    button.disabled = false;
  }
});
