return {
  {
    "neovim/nvim-lspconfig",
    event = { "BufReadPre", "BufNewFile" },
    dependencies = {
      "mason.nvim",
      "mason-lspconfig.nvim",
    },
    config = function()
      -- Set up keymaps when LSP attaches
      vim.api.nvim_create_autocmd("LspAttach", {
        group = vim.api.nvim_create_augroup("UserLspConfig", {}),
        callback = function(ev)
          local opts = { buffer = ev.buf }
          vim.keymap.set("n", "gd", vim.lsp.buf.definition, opts)
          vim.keymap.set("n", "K", vim.lsp.buf.hover, opts)
          vim.keymap.set("n", "<leader>rn", vim.lsp.buf.rename, opts)
          vim.keymap.set("n", "<leader>ca", vim.lsp.buf.code_action, opts)
          vim.keymap.set("n", "gr", "<cmd>Telescope lsp_references<cr>", opts)

          -- Enable inlay hints if supported
          local client = vim.lsp.get_client_by_id(ev.data.client_id)
          if client and client.server_capabilities.inlayHintProvider then
            vim.lsp.inlay_hint.enable(true, { bufnr = ev.buf })
          end
        end,
      })

      -- Configure specific language servers
      vim.lsp.config("lua_ls", {})
      vim.lsp.config("pyright", {})
      vim.lsp.config("ts_ls", {})
      vim.lsp.config("rust_analyzer", {})
      vim.lsp.config("gopls", {})

      -- ty: Astral's fast Python type checker (not in Mason, installed via uv/pip)
      vim.lsp.config("ty", {})
      vim.lsp.enable("ty")
    end,
  },

  {
    "williamboman/mason.nvim",
    cmd = "Mason",
    config = function()
      require("mason").setup()
    end,
  },

  {
    "williamboman/mason-lspconfig.nvim",
    dependencies = { "mason.nvim" },
    config = function()
      require("mason-lspconfig").setup({
        ensure_installed = {
          "lua_ls",
          "pyright",
          "ts_ls",
          "rust_analyzer",
          "gopls",
        },
        automatic_installation = true,
      })
    end,
  },
}
