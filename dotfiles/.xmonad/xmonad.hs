import Control.Applicative
import Control.Monad
import Data.Char
import Data.List
import Data.List.Split
import System.Exit
import XMonad
import XMonad.Actions.CycleWS
import XMonad.Actions.PhysicalScreens
import XMonad.Actions.UpdateFocus
import XMonad.Actions.UpdatePointer
import XMonad.Hooks.EwmhDesktops
import XMonad.Hooks.ManageDocks
import XMonad.Layout.Fullscreen
import XMonad.Layout.NoBorders
import XMonad.Layout.Reflect
import XMonad.Prompt
import XMonad.Prompt.ConfirmPrompt
import XMonad.Prompt.Pass
import XMonad.Prompt.Shell
import XMonad.Util.Run
import qualified Data.Map as M
import qualified Data.Traversable as T
import qualified XMonad.StackSet as W

main = xmonad $ ewmh $ docks $ fullscreenSupport $ def
  { terminal           = "alacritty"
  -- https://hackage.haskell.org/package/xmonad-contrib-0.13/docs/XMonad-Actions-UpdateFocus.html
  , startupHook        = adjustEventInput
  , handleEventHook    = focusOnMouseMove
  , layoutHook         = avoidStruts myLayout
  , logHook            = updatePointer (0.5, 0.5) (0.8, 0.8)
  , workspaces         = show <$> [0..9]
  , XMonad.keys        = myKeys
  , normalBorderColor  = "#839496"
  , focusedBorderColor = "#dc322f"
  }

myLayout = (noBorders Full) ||| (smartBorders tiled) ||| (smartBorders $ Mirror tiled)
  where
    tiled = reflectVert $ Tall nmaster delta ratio
    nmaster = 1
    ratio = 1/2
    delta = 3/100

promptCfg = def
  { position          = Top
  , alwaysHighlight   = True
  , promptBorderWidth = 1
  , font              = "xft:monospace:size=9"
  , bgColor           = "#002b36"
  , bgHLight          = "#073642"
  , fgColor           = "#839496"
  , fgHLight          = "#268bd2"
  , borderColor       = "#839496"
  }

searchCfg = promptCfg
  { searchPredicate = insensitive isInfixOf
  }

insensitive :: (String -> String -> Bool) -> (String -> String -> Bool)
insensitive f x y = f (map toLower x) (map toLower y)

data FilenamePrompt = FilenamePrompt

instance XPrompt FilenamePrompt where
  showXPrompt FilenamePrompt = "Filename: "

noCompletion :: ComplFunction
noCompletion _ = return []

screenshot :: String -> X()
screenshot fn = safeSpawn "maim" ["-u", "-s", fn ++ ".png"]

data MoshPrompt = MoshPrompt
instance XPrompt MoshPrompt where
  showXPrompt MoshPrompt = "moshen "

data SshPrompt = SshPrompt
instance XPrompt SshPrompt where
  showXPrompt SshPrompt = "sshen "

data MinicomPrompt = MinicomPrompt
instance XPrompt MinicomPrompt where
  showXPrompt MinicomPrompt = "ssh-minicom "

data TwitchPrompt = TwitchPrompt
instance XPrompt TwitchPrompt where
  showXPrompt TwitchPrompt = "twitch "

mosh :: String -> X()
mosh host = safeSpawn "alacritty" $ ["-e", "moshen"] ++ (splitOn " " host)

ssh :: String -> X()
ssh host = safeSpawn "alacritty" $ ["-e", "sshen"] ++ (splitOn " " host)

sshMinicom :: String -> X()
sshMinicom host = safeSpawn "alacritty" ["-e", "ssh", "-t", host, "--", "minicom", "-c", "on"]

twitch :: String -> X()
twitch username = safeSpawn "streamlink" ["https://twitch.tv/" ++ username]

myKeys conf@(XConfig {modMask = modMask}) = M.fromList $
  [ ((modMask .|. shiftMask                , xK_Return), spawn $ terminal conf)
  , ((modMask                              , xK_p     ), shellPrompt searchCfg)
  , ((modMask .|. shiftMask                , xK_p     ), passPrompt searchCfg)
  , ((modMask .|. shiftMask                , xK_y     ), spawn "yubikey-oath-dmenu --type")
  , ((modMask                              , xK_s     ), mkXPrompt FilenamePrompt promptCfg noCompletion screenshot)
  , ((modMask               .|. controlMask, xK_s     ), safeSpawn "screenclip" [])
  , ((modMask .|. shiftMask                , xK_s     ), mkXPrompt MoshPrompt promptCfg noCompletion mosh) -- TODO maybe complete on hosts somehow?
  , ((modMask .|. shiftMask .|. controlMask, xK_s     ), mkXPrompt SshPrompt promptCfg noCompletion ssh)
  , ((modMask .|. shiftMask                , xK_m     ), safeSpawn "alacritty" ["-e", "minicom"])
  , ((modMask .|. shiftMask .|. controlMask, xK_m     ), mkXPrompt MinicomPrompt promptCfg noCompletion sshMinicom)
  , ((modMask .|. shiftMask                , xK_t     ), mkXPrompt TwitchPrompt promptCfg noCompletion twitch)
  , ((modMask                              , xK_r     ), spawn "slop")
  , ((modMask .|. shiftMask                , xK_c     ), kill)
  , ((modMask                              , xK_space ), sendMessage NextLayout)
  , ((modMask                              , xK_n     ), refresh)
  , ((modMask                              , xK_j     ), windows W.focusDown)
  , ((modMask                              , xK_k     ), windows W.focusUp)
  , ((modMask                              , xK_m     ), windows W.focusMaster)
  , ((modMask .|. shiftMask                , xK_j     ), windows W.swapDown)
  , ((modMask .|. shiftMask                , xK_k     ), windows W.swapUp)
  , ((modMask                              , xK_Return), windows W.swapMaster)
  , ((modMask               .|. mod4Mask   , xK_Left  ), prevWS)
  , ((modMask               .|. mod4Mask   , xK_Right ), nextWS)
  , ((modMask .|. shiftMask .|. mod4Mask   , xK_Left  ), shiftToPrev)  -- shiftToPrev >> prevWS to switch after
  , ((modMask .|. shiftMask .|. mod4Mask   , xK_Right ), shiftToNext)
  , ((modMask                              , xK_h     ), sendMessage Shrink)
  , ((modMask                              , xK_l     ), sendMessage Expand)
  , ((modMask                              , xK_t     ), withFocused $ windows . W.sink)
  , ((modMask                              , xK_comma ), sendMessage (IncMasterN 1))
  , ((modMask                              , xK_period), sendMessage (IncMasterN (-1)))
  , ((modMask .|. shiftMask                , xK_q     ), confirmPrompt promptCfg "exit" $ io (exitWith ExitSuccess))
  , ((modMask                              , xK_q     ), confirmPrompt promptCfg "restart xmonad" $ spawn "if type xmonad; then xmonad --recompile && xmonad --restart; else xmessage xmonad not in \\$PATH: \"$PATH\"; fi")
  ]
  ++
  -- mod-{a,o,e,u,i} %! Switch to physical screen 1,2,3,4,5
  -- mod-shift-{a,o,e,u,i} %! Move client to screen 1,2,3,4,5
  -- [ ((m .|. modMask, key), screenWorkspace sc >>? () $ windows . f)
  [ (( m .|. modMask, key), getScreen scrOrd sc >>>= screenWorkspace >>? () $ windows . f)
  | (sc, key) <- zip [0..] [xK_a, xK_o, xK_e, xK_u, xK_i]
  , (f, m)    <- [(W.view, 0), (W.shift, shiftMask)]
  ]
  ++
  -- mod-numpad[0..9] %! Switch current screen to workspace 0..9
  --   NB: currently, if the workspace is visible, just focuses the screen that has it
  -- mod-shift-numpad[0..9] %! Move client to workspace 0..9
  [ ((m .|. modMask, key), windows $ f $ show ws)
  | (ws, key) <- zip [0..] [xK_KP_Insert, xK_KP_End, xK_KP_Down, xK_KP_Next, xK_KP_Left, xK_KP_Begin, xK_KP_Right, xK_KP_Home, xK_KP_Up, xK_KP_Prior]
  , (f, m)    <- [(W.view, 0), (W.shift, shiftMask)]
  ]
    where
      scrOrd = screenComparatorByRectangle cmp
        where
          -- TODO make this sort clockwise from bottom left
          cmp (Rectangle x1 y1 _ _) (Rectangle x2 y2 _ _) = compare (-y1, x1) (-y2, x2)

-- >>=, but unwrapping Maybe with a default value
(>>?) :: Monad m => m (Maybe a) -> b -> (a -> m b) -> m b
(l >>? d) f = l >>= (maybe (return d) f)
infixl 1 >>?

-- https://stackoverflow.com/a/28215697
(>>>=) :: (Monad m, Traversable t, Monad t) => m (t a) -> (a -> m (t b)) -> m (t b)
x >>>= f = joinT $ liftMM f x
  where
    joinT = (>>= liftM join . T.sequence)
    liftMM = liftM . liftM
infixl 1 >>>=
