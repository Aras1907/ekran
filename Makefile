.PHONY: run clean flatpak-build flatpak-run flatpak-clean flatpak-publish

run:
	python3 -m ekran.main

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +

flatpak-build:
	flatpak-builder --force-clean --user --install build io.github.ekran.Ekran.yml

flatpak-run:
	flatpak run io.github.ekran.Ekran

flatpak-clean:
	rm -rf build .flatpak-builder

flatpak-publish:
	flatpak-builder --repo=ekran-repo --force-clean --disable-cache build io.github.ekran.Ekran.yml
	cd ekran-repo && git add -A && git commit -m "Update OSTree repo" && git push origin main
