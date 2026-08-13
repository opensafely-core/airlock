document.querySelector('[data-testid="loginform"]')?.addEventListener('submit', () => {
  document.getElementById("login-button").classList.add("hidden");
  document.getElementById("spinner").classList.remove("hidden");
});
