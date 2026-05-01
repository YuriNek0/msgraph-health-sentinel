{ config, lib, pkgs, ... }:
let
  cfg = config.services.msgraph-health-sentinel;

  defaultPackage = pkgs.writeShellApplication {
    name = "msgraph-health-sentinel";
    runtimeInputs = [
      (pkgs.python3.withPackages (ps: [ ps.requests ]))
    ];
    text = ''
      exec python "${./../../fetch.py}" "$@"
    '';
  };

  effectivePackage = if cfg.package == null then defaultPackage else cfg.package;
  escapedArgs = lib.escapeShellArgs cfg.extraArgs;
in
{
  options.services.msgraph-health-sentinel = {
    enable = lib.mkEnableOption "MSGraph Health Sentinel user service";

    package = lib.mkOption {
      type = lib.types.nullOr lib.types.package;
      default = null;
      description = "Package providing the msgraph-health-sentinel executable.";
    };

    configFile = lib.mkOption {
      type = lib.types.str;
      default = "";
      example = "$HOME/.config/msgraph-health-sentinel/config.json";
      description = ''
        Path to the runtime JSON config file used by MSGraph Health Sentinel.
        Keep this outside of the Nix store because it contains credentials.
      '';
    };

    extraArgs = lib.mkOption {
      type = with lib.types; listOf str;
      default = [ ];
      example = [ "--once" ];
      description = "Extra CLI arguments passed to msgraph-health-sentinel.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.configFile != "";
        message = "services.msgraph-health-sentinel.configFile must be set when enabling the service.";
      }
    ];

    systemd.user.services.msgraph-health-sentinel = {
      Unit = {
        Description = "MSGraph Health Sentinel";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
        StartLimitIntervalSec = "10min";
        StartLimitBurst = 3;
      };

      Service = {
        Type = "simple";
        ExecStart =
          "${effectivePackage}/bin/msgraph-health-sentinel --config ${lib.escapeShellArg cfg.configFile}"
          + lib.optionalString (cfg.extraArgs != [ ]) " ${escapedArgs}";
        Restart = "always";
        RestartSec = "10s";
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
