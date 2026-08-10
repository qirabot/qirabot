import { defineConfig } from 'vitepress'
import llmstxt from 'vitepress-plugin-llms'

// Docs site for qirabot.com/docs. Deployed by .github/workflows/docs-deploy.yml
// to the website S3 bucket under the docs/ prefix; the marketing site's
// deploy-s3.sh excludes docs/* so the two deploys never touch each other.
// URLs keep the .html suffix (cleanUrls: false, the default) because the S3
// REST origin serves exact keys; directory URLs (/docs/, /docs/guide/) are
// rewritten to index.html by the qirabot-website-dir-index CloudFront function.
//
// i18n: English is the root locale (/docs/...), Chinese lives under /docs/zh/.
// Every page must exist in both trees — add new pages to BOTH sidebars below.

const enSidebar = [
  {
    text: 'Getting Started',
    items: [
      { text: 'Installation', link: '/guide/installation' },
      { text: 'Quick Start', link: '/guide/quickstart' },
      { text: 'Upgrading from v2', link: '/guide/migration-v3' },
      { text: 'CLI Reference', link: '/guide/cli' },
      { text: 'Use with AI Agents', link: '/guide/agents' },
      { text: 'Demos', link: '/demos' },
    ],
  },
  {
    text: 'Platforms',
    items: [
      { text: 'Browser', link: '/backends/browser' },
      { text: 'Android', link: '/backends/android' },
      { text: 'iOS', link: '/backends/ios' },
      { text: 'Windows & Games', link: '/backends/windows-games' },
      { text: 'Desktop', link: '/backends/desktop' },
      { text: 'Custom Adapters', link: '/backends/custom-adapters' },
    ],
  },
  {
    text: 'Integrations',
    items: [
      { text: 'Playwright', link: '/frameworks/playwright' },
      { text: 'Selenium', link: '/frameworks/selenium' },
      { text: 'Appium', link: '/frameworks/appium' },
      { text: 'pytest', link: '/frameworks/pytest' },
    ],
  },
  {
    text: 'Advanced',
    items: [
      { text: 'AI Tasks & Custom Tools', link: '/advanced/ai-tasks' },
      { text: 'Progress Overlay & Kill Switch', link: '/advanced/overlay' },
      { text: 'Reports & Recording', link: '/advanced/reports' },
      { text: 'Configuration', link: '/advanced/configuration' },
      { text: 'Controlling Cost', link: '/advanced/cost' },
      { text: 'Error Handling', link: '/advanced/error-handling' },
      { text: 'FAQ', link: '/advanced/faq' },
    ],
  },
  {
    text: 'Reference',
    items: [
      { text: 'API — Actions & Platforms', link: '/reference/api' },
      { text: 'Methods — Signatures & Returns', link: '/reference/methods' },
      { text: 'Data & Privacy', link: '/reference/privacy' },
    ],
  },
]

const zhSidebar = [
  {
    text: '快速上手',
    items: [
      { text: '安装', link: '/zh/guide/installation' },
      { text: '快速开始', link: '/zh/guide/quickstart' },
      { text: '从 v2 升级', link: '/zh/guide/migration-v3' },
      { text: 'CLI 参考', link: '/zh/guide/cli' },
      { text: '配合 AI Agent 使用', link: '/zh/guide/agents' },
      { text: '实战演示', link: '/zh/demos' },
    ],
  },
  {
    text: '支持平台',
    items: [
      { text: '浏览器', link: '/zh/backends/browser' },
      { text: 'Android', link: '/zh/backends/android' },
      { text: 'iOS', link: '/zh/backends/ios' },
      { text: 'Windows 与游戏', link: '/zh/backends/windows-games' },
      { text: '桌面', link: '/zh/backends/desktop' },
      { text: '自定义 Adapter', link: '/zh/backends/custom-adapters' },
    ],
  },
  {
    text: '框架集成',
    items: [
      { text: 'Playwright', link: '/zh/frameworks/playwright' },
      { text: 'Selenium', link: '/zh/frameworks/selenium' },
      { text: 'Appium', link: '/zh/frameworks/appium' },
      { text: 'pytest', link: '/zh/frameworks/pytest' },
    ],
  },
  {
    text: '进阶',
    items: [
      { text: 'AI 任务与自定义工具', link: '/zh/advanced/ai-tasks' },
      { text: '进度悬浮窗与急停', link: '/zh/advanced/overlay' },
      { text: '报告与录屏', link: '/zh/advanced/reports' },
      { text: '配置', link: '/zh/advanced/configuration' },
      { text: '控制成本', link: '/zh/advanced/cost' },
      { text: '错误处理', link: '/zh/advanced/error-handling' },
      { text: '常见问题 FAQ', link: '/zh/advanced/faq' },
    ],
  },
  {
    text: '参考',
    items: [
      { text: 'API——动作与平台', link: '/zh/reference/api' },
      { text: '方法参考——签名与返回值', link: '/zh/reference/methods' },
      { text: '数据与隐私', link: '/zh/reference/privacy' },
    ],
  },
]

export default defineConfig({
  base: '/docs/',
  title: 'Qirabot Docs',
  description:
    'AI vision-driven GUI automation for browsers, Android, iOS, desktops, and games — no DOM, no selectors. Python SDK and CLI.',
  head: [
    [
      'link',
      {
        rel: 'icon',
        type: 'image/svg+xml',
        // Same geometric "Q" favicon as the marketing site (see website/index.html).
        href: 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><defs><linearGradient id=%22g%22 x1=%220%22 y1=%220%22 x2=%221%22 y2=%221%22><stop offset=%220%22 stop-color=%22%238b5cf6%22/><stop offset=%221%22 stop-color=%22%236366f1%22/></linearGradient></defs><rect width=%22100%22 height=%22100%22 rx=%2222%22 fill=%22url(%23g)%22/><circle cx=%2250%22 cy=%2246%22 r=%2221%22 fill=%22none%22 stroke=%22white%22 stroke-width=%2211%22/><line x1=%2257%22 y1=%2253%22 x2=%2274%22 y2=%2271%22 stroke=%22white%22 stroke-width=%2211%22 stroke-linecap=%22round%22/></svg>',
      },
    ],
    // Social cards. og:title / og:description are injected per page by
    // transformPageData below; the image is shared site-wide.
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Qirabot Docs' }],
    ['meta', { property: 'og:image', content: 'https://qirabot.com/docs/og-image.png' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: 'https://qirabot.com/docs/og-image.png' }],
  ],
  transformPageData(pageData, { siteConfig }) {
    const site = siteConfig.site
    const locale = pageData.relativePath.startsWith('zh/') ? site.locales.zh : site.locales.root
    const title = pageData.frontmatter.title || pageData.title || locale.title || site.title
    const description = pageData.frontmatter.description || locale.description || site.description
    pageData.frontmatter.head ??= []
    // Canonical URL. URLs keep the .html suffix (cleanUrls: false) but the
    // CloudFront dir-index function also serves /docs/ and /docs/zh/, so
    // without this the same page is reachable under several URLs. Must match
    // the sitemap's <loc> exactly: index.md maps to the bare directory.
    const canonicalPath = pageData.relativePath
      .replace(/(^|\/)index\.md$/, '$1')
      .replace(/\.md$/, '.html')
    pageData.frontmatter.head.push(
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: description }],
      ['link', { rel: 'canonical', href: `https://qirabot.com/docs/${canonicalPath}` }],
    )
  },
  lastUpdated: true,
  sitemap: {
    hostname: 'https://qirabot.com/docs/',
  },
  vite: {
    plugins: [
      // llms.txt + llms-full.txt for AI tools (Cursor @Docs, Claude, etc.).
      // English pages only — LLM consumers read English; halves the token cost.
      llmstxt({
        domain: 'https://qirabot.com',
        ignoreFiles: ['zh/**/*'],
        description:
          'AI vision-driven GUI automation for browsers, Android, iOS, desktops, and games — no DOM, no selectors. Python SDK and CLI.',
      }),
    ],
  },
  locales: {
    root: {
      label: 'English',
      lang: 'en',
      themeConfig: {
        nav: [
          { text: 'Guide', link: '/guide/quickstart' },
          { text: 'Platforms', link: '/backends/browser' },
          { text: 'Demos', link: '/demos' },
        ],
        sidebar: enSidebar,
        editLink: {
          pattern: 'https://github.com/qirabot/qirabot/edit/main/docs/:path',
          text: 'Edit this page on GitHub',
        },
        footer: {
          message: 'Released under the MIT License.',
          copyright: '© Qirabot Platform',
        },
      },
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      title: 'Qirabot 文档',
      description:
        'AI 视觉驱动的跨端 GUI 自动化——浏览器、Android、iOS、桌面与游戏。无需 DOM、无需选择器。Python SDK 与 CLI。',
      themeConfig: {
        nav: [
          { text: '指南', link: '/zh/guide/quickstart' },
          { text: '支持平台', link: '/zh/backends/browser' },
          { text: '实战演示', link: '/zh/demos' },
        ],
        sidebar: zhSidebar,
        editLink: {
          pattern: 'https://github.com/qirabot/qirabot/edit/main/docs/:path',
          text: '在 GitHub 上编辑此页',
        },
        footer: {
          message: '基于 MIT 许可证发布。',
          copyright: '© Qirabot Platform',
        },
        outline: { label: '本页目录' },
        docFooter: { prev: '上一页', next: '下一页' },
        darkModeSwitchLabel: '外观',
        sidebarMenuLabel: '菜单',
        returnToTopLabel: '返回顶部',
        langMenuLabel: '切换语言',
        lastUpdated: { text: '最后更新' },
      },
    },
  },
  themeConfig: {
    logo: '/logo.svg',
    socialLinks: [{ icon: 'github', link: 'https://github.com/qirabot/qirabot' }],
    search: {
      provider: 'local',
      options: {
        locales: {
          zh: {
            translations: {
              button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
              modal: {
                noResultsText: '没有找到结果',
                resetButtonTitle: '清除搜索条件',
                footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' },
              },
            },
          },
        },
      },
    },
  },
})
