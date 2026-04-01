export default {
  name: "MyApp",
  id: "com.datacore.myapp",
  version: "0.1.0",

  ai: { provider: "claude" as const },

  licensing: {
    type: "one-time" as const,
    price: { usd: 49 },
    trial: { days: 14 },
  },

  dataDir: {
    standalone: "~/MyApp",
    datacore: "0-personal/myapp",
  },
}
