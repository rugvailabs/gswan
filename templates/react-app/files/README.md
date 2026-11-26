# App

A web app that runs in the browser (React + TypeScript + Tailwind CSS + Zustand, built with Vite).

## Run it
```
npm install      # once: downloads the libraries into node_modules/
npm run dev      # starts the app; open the address it prints (usually http://localhost:5173)
npm test         # runs the tests
npm run build    # checks the types and writes the finished site to dist/
```

## What is where
- `index.html` - the page shell; the app is drawn into its `#root` element.
- `src/main.tsx` - starts the app.
- `src/App.tsx` - the screen you see. Start changing things here.
- `src/store.ts` - app state (a small Zustand store). Add more stores next to it.
- `src/index.css` - styles. Tailwind classes are written straight in the components.
- `src/*.test.tsx` - tests (Vitest + Testing Library). `src/test-setup.ts` runs before them.
- `vite.config.ts` - build and test settings. `tsconfig.json` - TypeScript settings (strict).
