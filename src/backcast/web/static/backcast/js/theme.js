// Runs before the stylesheet so a stored theme never flashes the wrong colours.
try {
  const theme = localStorage.getItem("backcast-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {}
