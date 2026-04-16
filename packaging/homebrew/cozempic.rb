class Cozempic < Formula
  include Language::Python::Virtualenv

  desc "Context cleaning CLI for Claude Code — prune bloat, protect agent teams"
  homepage "https://github.com/Ruya-AI/cozempic"
  url "https://files.pythonhosted.org/packages/24/94/3b77492917cd8f6583ab3564767eefbe7025ad912201945be80cc4df2e1e/cozempic-1.7.1.tar.gz"
  sha256 "9878ca1e0ddde69b478d66f0ce0b4d5abb49ecc105f493e51fffa0a738436a88"
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      Cozempic is installed but not yet wired into your Claude Code settings.
      To enable background protection on every Claude Code session in every project:

        cozempic init --global

      Or just run any cozempic command (e.g. `cozempic --version`) and you'll be
      prompted on first use.

      Opt out entirely with:

        export COZEMPIC_NO_GLOBAL_INIT=1
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/cozempic --version")
    assert_match "diagnose", shell_output("#{bin}/cozempic --help")
  end
end
