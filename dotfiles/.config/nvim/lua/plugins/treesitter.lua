return {
  {
    "nvim-treesitter/nvim-treesitter",
    build = ":TSUpdate", -- Automatically update parsers after install
    event = { "BufReadPost", "BufNewFile" }, -- Load when opening files
    config = function()
      require("nvim-treesitter.configs").setup({
        ensure_installed = {
          "lua",
          "python",
          "javascript",
          "typescript",
          "rust",
          "go",
          "c",
          "cpp",
          "html",
          "css",
          "json",
          "yaml",
          "markdown",
          "bash",
        },
        auto_install = true, -- Automatically install missing parsers
        highlight = {
          enable = true,
          additional_vim_regex_highlighting = false,
        },
        indent = {
          enable = true,
        },
      })
    end,
  },
}
