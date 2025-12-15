return {
  {
    "nvim-treesitter/nvim-treesitter",
    build = ":TSUpdate", -- Automatically update parsers after install
    event = { "BufReadPost", "BufNewFile" }, -- Load when opening files
    dependencies = {
      "nvim-treesitter/nvim-treesitter-textobjects",
    },
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
        textobjects = {
          select = {
            enable = true,
            lookahead = true, -- Jump forward to textobj if not on one
            keymaps = {
              ["af"] = "@function.outer", -- around function
              ["if"] = "@function.inner", -- inside function
              ["ac"] = "@class.outer", -- around class
              ["ic"] = "@class.inner", -- inside class
              ["aa"] = "@parameter.outer", -- around argument/parameter
              ["ia"] = "@parameter.inner", -- inside argument/parameter
              ["ai"] = "@conditional.outer", -- around if/conditional
              ["ii"] = "@conditional.inner", -- inside if/conditional
              ["al"] = "@loop.outer", -- around loop
              ["il"] = "@loop.inner", -- inside loop
            },
          },
          move = {
            enable = true,
            set_jumps = true, -- Add to jumplist
            goto_next_start = {
              ["]f"] = "@function.outer",
              ["]c"] = "@class.outer",
              ["]a"] = "@parameter.inner",
            },
            goto_next_end = {
              ["]F"] = "@function.outer",
              ["]C"] = "@class.outer",
            },
            goto_previous_start = {
              ["[f"] = "@function.outer",
              ["[c"] = "@class.outer",
              ["[a"] = "@parameter.inner",
            },
            goto_previous_end = {
              ["[F"] = "@function.outer",
              ["[C"] = "@class.outer",
            },
          },
          swap = {
            enable = true,
            swap_next = {
              ["<leader>sa"] = "@parameter.inner", -- swap argument with next
            },
            swap_previous = {
              ["<leader>sA"] = "@parameter.inner", -- swap argument with previous
            },
          },
        },
      })
    end,
  },
}
