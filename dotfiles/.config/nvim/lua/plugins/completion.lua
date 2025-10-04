return {
  {
    "hrsh7th/nvim-cmp",
    event = "InsertEnter", -- Load only when entering insert mode
    dependencies = {
      "hrsh7th/cmp-nvim-lsp", -- LSP completion source
      "hrsh7th/cmp-buffer", -- Buffer text completion
      "hrsh7th/cmp-path", -- File path completion
      "hrsh7th/cmp-cmdline", -- Command line completion
      "L3MON4D3/LuaSnip", -- Snippet engine
      "saadparwaiz1/cmp_luasnip", -- Snippet completion source
    },
    config = function()
      local cmp = require("cmp")
      local luasnip = require("luasnip")

      cmp.setup({
        snippet = {
          expand = function(args)
            luasnip.lsp_expand(args.body)
          end,
        },
        mapping = cmp.mapping.preset.insert({
          ["<C-b>"] = cmp.mapping.scroll_docs(-4),
          ["<C-f>"] = cmp.mapping.scroll_docs(4),
          ["<C-Space>"] = cmp.mapping.complete(),
          ["<C-e>"] = cmp.mapping.abort(),
          ["<CR>"] = cmp.mapping.confirm({ select = true }),
          -- Tab through suggestions
          ["<Tab>"] = cmp.mapping(function(fallback)
            if cmp.visible() then
              cmp.select_next_item()
            elseif luasnip.expand_or_jumpable() then
              luasnip.expand_or_jump()
            else
              fallback()
            end
          end, { "i", "s" }),
          ["<S-Tab>"] = cmp.mapping(function(fallback)
            if cmp.visible() then
              cmp.select_previous_item()
            elseif luasnip.jumpable(-1) then
              luasnip.jump(-1)
            else
              fallback()
            end
          end, { "i", "s" }),
        }),
        sources = cmp.config.sources({
          { name = "nvim_lsp" }, -- LSP completions (highest priority)
          { name = "luasnip" }, -- Snippets
        }, {
          { name = "buffer" }, -- Buffer text (fallback)
          { name = "path" }, -- File paths
        }),
      })
    end,
  },
}
