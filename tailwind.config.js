/*
 * The Tailwind build configuration (D93).
 *
 * The standalone Tailwind CLI reads this file: `content` lists every place a
 * class name can be written, and the theme holds the shop's own colours and
 * font stack. The values below are the ones the pinned CDN was given in
 * templates/base.html, moved here unchanged, so no page changes its look.
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      // Tailwind's own sans stack stays first, so Latin text is unchanged;
      // the Devanagari face at the end draws Hindi (D92).
      fontFamily: {
        sans: [
          "ui-sans-serif", "system-ui", "sans-serif",
          '"Apple Color Emoji"', '"Segoe UI Emoji"', '"Segoe UI Symbol"',
          '"Noto Color Emoji"', '"Noto Sans Devanagari"',
        ],
      },
      colors: {
        brand: {
          50: "#fef7ed",
          100: "#fdebd0",
          500: "#ea580c",
          600: "#c2410c",
          700: "#9a3412",
        },
      },
    },
  },
};
